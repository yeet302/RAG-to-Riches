#!/usr/bin/env bash
# Key Vault, one identity per service, and least-privilege access. Safe to re-run.
#
#   cp security/azure.env.example security/azure.env    # fill in names / resource IDs
#   az login
#   bash security/setup_azure.sh all                     # or one part: vault | identities | roles | team | show
#
# Works in Git Bash on Windows, macOS and Linux. Needs Azure CLI 2.60+ and, on each scope it
# grants access to, Owner / User Access Administrator / Role Based Access Control Administrator.
# Resources owned by a teammate: run `roles` from their account, or ask them to.
set -euo pipefail
export MSYS_NO_PATHCONV=1      # Git Bash: keep /subscriptions/... IDs from turning into C:/Program Files/...

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${AZURE_SETUP_CONFIG:-$HERE/azure.env}"
[ -f "$CONFIG" ] || { echo "Missing $CONFIG. Copy security/azure.env.example to security/azure.env and fill it in."; exit 1; }
# Read KEY=VALUE lines without running them as shell code (values may contain spaces).
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"                                  # tolerate Windows line endings
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  key="${line%%=*}"; value="${line#*=}"
  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || { echo "Bad line in $CONFIG: $line"; exit 1; }
  value="${value#\"}"; value="${value%\"}"
  export "$key=$value"
done < "$CONFIG"
: "${RESOURCE_GROUP:?set RESOURCE_GROUP}" "${LOCATION:?set LOCATION}" "${KEY_VAULT_NAME:?set KEY_VAULT_NAME}"
[ -n "${SUBSCRIPTION_ID:-}" ] && az account set --subscription "$SUBSCRIPTION_ID"

SERVICES=(orchestrator logs docs code)
# Which Key Vault secrets each service may read. Keep in sync with SERVICE_SECRETS in service_secrets.py.
secrets_of() {
  case "$1" in
    orchestrator) echo "orchestrator-azure-ai-api-key logs-agent-token docs-agent-token code-agent-token" ;;
    logs) echo "logs-agent-token" ;;
    docs) echo "docs-agent-token docs-azure-ai-api-key" ;;
    code) echo "code-agent-token code-github-token" ;;
  esac
}

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok   %s\n' "$*"; }
skip() { printf '   skip %s\n' "$*"; }
warn() { printf '   WARN %s\n' "$*"; }

vault_id() { az keyvault show -n "$KEY_VAULT_NAME" --query id -o tsv; }
identity_name() { echo "id-rag-$1"; }
principal_of() { az identity show -g "$RESOURCE_GROUP" -n "$(identity_name "$1")" --query principalId -o tsv; }

grant() {  # grant <principal-object-id> <principal-type> <role> <scope> <description>
  local err; err="$(mktemp)"
  if az role assignment create --assignee-object-id "$1" --assignee-principal-type "$2" \
       --role "$3" --scope "$4" --only-show-errors -o none 2>"$err"; then
    ok "$5: $3"
  else
    warn "$5: could not grant '$3' ($(head -c 200 "$err"))"
    warn "  -> the owner of that resource can run: az role assignment create --assignee-object-id $1 --assignee-principal-type $2 --role \"$3\" --scope $4"
  fi
  rm -f "$err"
}

random_token() {
  python -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null \
    || python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null \
    || openssl rand -base64 32 | tr -d '\n='
}

put_secret() {  # put_secret <name> <value>  (value goes through a private temp file, never argv)
  local tmp; tmp="$(mktemp)"; chmod 600 "$tmp"
  printf '%s' "$2" > "$tmp"
  az keyvault secret set --vault-name "$KEY_VAULT_NAME" -n "$1" --file "$tmp" --encoding utf-8 -o none --only-show-errors
  rm -f "$tmp"
}

secret_exists() { az keyvault secret show --vault-name "$KEY_VAULT_NAME" -n "$1" --query id -o tsv >/dev/null 2>&1; }

do_vault() {
  say "Resource group and Key Vault ($KEY_VAULT_NAME, access by Azure RBAC)"
  az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none --only-show-errors
  if az keyvault show -n "$KEY_VAULT_NAME" -o none 2>/dev/null; then
    ok "Key Vault exists"
  else
    az keyvault create -n "$KEY_VAULT_NAME" -g "$RESOURCE_GROUP" -l "$LOCATION" \
      --enable-rbac-authorization true --retention-days 7 -o none --only-show-errors
    ok "Key Vault created"
  fi
  local me; me="$(az ad signed-in-user show --query id -o tsv)"
  grant "$me" User "Key Vault Secrets Officer" "$(vault_id)" "you (to create secrets)"
  echo "   (new role assignments can take a few minutes to apply; re-run if a secret write is refused)"

  say "Agent tokens: one random token per agent, never reused"
  for agent in logs docs code; do
    if secret_exists "$agent-agent-token"; then ok "$agent-agent-token exists (kept)"
    else put_secret "$agent-agent-token" "$(random_token)"; ok "$agent-agent-token created"; fi
  done

  say "API keys (typed input is hidden; press Enter to skip one, e.g. when that service uses az login)"
  for name in orchestrator-azure-ai-api-key docs-azure-ai-api-key code-github-token; do
    if secret_exists "$name"; then ok "$name exists (kept; delete it in the portal to replace)"; continue; fi
    local value=""
    read -r -s -p "   value for $name: " value || true; echo
    if [ -n "$value" ]; then put_secret "$name" "$value"; ok "$name stored"; else skip "$name"; fi
    unset value
  done
}

do_identities() {
  say "One managed identity per service, each allowed to read only its own secrets"
  local kv; kv="$(vault_id)"
  for svc in "${SERVICES[@]}"; do
    local name; name="$(identity_name "$svc")"
    az identity show -g "$RESOURCE_GROUP" -n "$name" -o none 2>/dev/null \
      || az identity create -g "$RESOURCE_GROUP" -n "$name" -l "$LOCATION" -o none --only-show-errors
    local pid; pid="$(principal_of "$svc")"
    ok "$name (principal $pid)"
    for secret in $(secrets_of "$svc"); do
      if secret_exists "$secret"; then
        grant "$pid" ServicePrincipal "Key Vault Secrets User" "$kv/secrets/$secret" "$name -> secret $secret"
      else
        skip "$name -> secret $secret (not in the vault)"
      fi
    done
  done
}

do_roles() {
  say "Each identity gets only the data and model access its service needs"
  local orch logs docs code
  orch="$(principal_of orchestrator)"; logs="$(principal_of logs)"; docs="$(principal_of docs)"; code="$(principal_of code)"
  role() {  # role <principal> <role> <resource-id-var> <description>
    local scope="${!3:-}"
    if [ -z "$scope" ]; then skip "$4 ($3 not set in azure.env)"; else grant "$1" ServicePrincipal "$2" "$scope" "$4"; fi
  }
  role "$orch" "Cognitive Services OpenAI User" ORCHESTRATOR_MODEL_RESOURCE_ID "orchestrator -> its planning/answer model"
  role "$docs" "Cognitive Services OpenAI User" DOCS_MODEL_RESOURCE_ID       "docs -> its model"
  role "$code" "Azure AI User"                  CODE_FOUNDRY_RESOURCE_ID      "code -> its Foundry project"
  role "$code" "Search Index Data Reader"       CODE_SEARCH_RESOURCE_ID       "code -> the code search index (read only)"
  role "$logs" "Azure AI User"                  LOGS_FOUNDRY_RESOURCE_ID      "logs -> its Foundry project"
  role "$logs" "Log Analytics Reader"           LOGS_WORKSPACE_RESOURCE_ID    "logs -> the Log Analytics workspace (read only)"
  echo "   Not granted on purpose: docs/logs get no Search access, code/docs get no log access, and"
  echo "   only the orchestrator can read the agent tokens of other agents."
}

do_team() {
  say "Teammates who run the services locally: read secrets (no write, no delete)"
  [ -z "${TEAM_MEMBERS:-}" ] && { skip "TEAM_MEMBERS is empty"; return; }
  local kv; kv="$(vault_id)"
  for upn in $TEAM_MEMBERS; do
    local oid; oid="$(az ad user show --id "$upn" --query id -o tsv 2>/dev/null || true)"
    if [ -z "$oid" ]; then warn "$upn not found in this tenant"; continue; fi
    grant "$oid" User "Key Vault Secrets User" "$kv" "$upn"
  done
}

do_show() {
  say "Put these in the .env files (and AZURE_CLIENT_ID in each hosted service)"
  echo "   KEY_VAULT_URL=$(az keyvault show -n "$KEY_VAULT_NAME" --query properties.vaultUri -o tsv)"
  for svc in "${SERVICES[@]}"; do
    local name; name="$(identity_name "$svc")"
    local cid; cid="$(az identity show -g "$RESOURCE_GROUP" -n "$name" --query clientId -o tsv 2>/dev/null || echo '(not created)')"
    echo "   $svc: AZURE_CLIENT_ID=$cid"
  done
  say "Access each identity has"
  for svc in "${SERVICES[@]}"; do
    local pid; pid="$(principal_of "$svc" 2>/dev/null || true)"
    [ -z "$pid" ] && continue
    echo "-- $(identity_name "$svc")"
    az role assignment list --assignee "$pid" --all \
      --query "[].{role:roleDefinitionName, scope:scope}" -o table | sed 's/^/   /'
  done
}

case "${1:-}" in
  vault) do_vault ;;
  identities) do_identities ;;
  roles) do_roles ;;
  team) do_team ;;
  show) do_show ;;
  all) do_vault; do_identities; do_roles; do_team; do_show ;;
  *) echo "usage: bash security/setup_azure.sh all|vault|identities|roles|team|show"; exit 2 ;;
esac
