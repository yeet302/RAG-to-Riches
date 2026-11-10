# Security setup: Key Vault, per-agent identities, authenticated agent calls

User story: *As the team, we want each agent to have its own identity and limited access, and all API
keys stored safely, so that logs, docs and code are only reachable by the right parts of the system.*

## What is protected, and how

**Keys live only in Azure Key Vault.** Each service reads its own keys at startup
(`service_secrets.py`). With `KEY_VAULT_URL` set, a service **refuses to start** if a key is also in
its `.env`, so keys can't quietly stay in config files.

| Key Vault secret | What it is | Who can read it |
|---|---|---|
| `logs-agent-token` | proves a caller may use the logs agent | logs agent, orchestrator |
| `docs-agent-token` | proves a caller may use the docs agent | docs agent, orchestrator |
| `code-agent-token` | proves a caller may use the code agent | code agent, orchestrator |
| `orchestrator-azure-ai-api-key` | orchestrator model key (optional: not needed with `AZURE_AI_AUTH_MODE=entra`) | orchestrator |
| `docs-azure-ai-api-key` | docs agent model key (optional, same reason) | docs agent |
| `code-github-token` | GitHub read-only token for the code agent (optional, avoids rate limits) | code agent |

**Each service has its own identity with only the access it needs.** These are created by
`security/setup_azure.sh`:

| Identity | Can reach | Cannot reach |
|---|---|---|
| `id-rag-orchestrator` | its model; the three agent tokens | logs, docs and code data directly |
| `id-rag-logs` | its Foundry project; the Log Analytics workspace (read only); `logs-agent-token` | code search index, other agents' secrets |
| `id-rag-docs` | its model; `docs-agent-token`, `docs-azure-ai-api-key` | logs, code search, other agents' secrets |
| `id-rag-code` | its Foundry project; the code search index (read only); `code-agent-token`, `code-github-token` | logs, other agents' secrets |

Key Vault access is granted **per secret**, not per vault. So the docs identity cannot read the
GitHub token, and the logs identity cannot read the docs key.

**The orchestrator authenticates every call to every agent.** Each agent has its own random token,
sent as `Authorization: Bearer …` and compared in constant time. With `REQUIRE_AGENT_AUTH=true`:
- **Each agent** refuses to start without its token.
- **The orchestrator** refuses to start unless:
  - every configured agent has a token;
  - all the tokens are different, so a leaked token opens only one agent;
  - agents on other machines use `https`.

`/api/health` shows `agent_auth`, `require_agent_auth` and `secrets_source`. It never shows values.

> **On a laptop**, all four services run as your own `az login`, so they share your access. The
> per-service identities take effect when the services run in Azure, where each one is given its own
> `AZURE_CLIENT_ID`. The access rules exist now, and `verify_security.py --identities` checks them.

## Part A: one-time Azure setup (one person)

You need **Owner** or **User Access Administrator** on the resource group, and the Azure CLI.

```bash
cp security/azure.env.example security/azure.env    # fill in: vault name, resource IDs, teammates
az login
bash security/setup_azure.sh all
```

The script is safe to re-run. It:
1. **Creates the Key Vault.** Access uses Azure roles (RBAC), not access policies.
2. **Creates the three agent tokens.** Each is a fresh 32-byte random value.
3. **Asks for the optional API keys.** Your typing is hidden, and values go through a private temp
   file, never the command line.
4. **Creates the four managed identities** and gives each "Key Vault Secrets User" on only its own
   secrets.
5. **Gives each identity its data and model roles.** Each one gets only the roles listed in the
   table above.
6. **Gives each teammate in `TEAM_MEMBERS`** read access to the secrets, so local runs work.
7. **Prints `KEY_VAULT_URL`** and each identity's `AZURE_CLIENT_ID`.

**Resources owned by a teammate** (for example John's model or Navya's search service): if you lack
rights there, the script prints the exact `az role assignment create …` command for that owner to
run. They can also run `bash security/setup_azure.sh roles` themselves.

New role assignments can take 5–10 minutes to start working.

## Part B: every teammate, on their laptop

1. **Sign in:** `az login` (with `--tenant <team tenant>` if you have several).
2. **Install the new packages:** `pip install -r orchestrator/requirements-dev.txt`. This adds
   `azure-keyvault-secrets` for all services.
3. **In all four `.env` files** (`orchestrator/`, `logs_agent/`, `docs_agent/`, `code_agent/`):
   - **Add:**
     ```dotenv
     KEY_VAULT_URL=https://<your-vault>.vault.azure.net/
     REQUIRE_AGENT_AUTH=true
     ```
   - **Delete these lines if present:** `AZURE_AI_API_KEY`, `GITHUB_TOKEN`, `LOGS_AGENT_TOKEN`,
     `DOCS_AGENT_TOKEN`, `CODE_AGENT_TOKEN`.
4. **Start everything as usual:** `python orchestrator/run_local.py`.
5. **Check:** `python security/verify_security.py --live`.

## Part C: verify (for the demo)

```bash
python security/verify_security.py                # 1. no keys in files  2. each service reads its own secrets
python security/verify_security.py --live         # 3. agents reject no token / wrong token / another agent's token
python security/verify_security.py --identities   # 4. each identity has exactly its roles
bash security/setup_azure.sh show                 # KEY_VAULT_URL, client IDs, every role assignment
```

The script prints PASS / WARN / FAIL. It exits with status 1 if anything fails.

## Rotating a key or token

1. In the Azure portal, open **Key Vault → Secrets → the secret → New version**. For a token,
   generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Restart the service that owns it. For an agent token, also restart the orchestrator.

Nothing else changes. No file edits and no commits.

## Running in Azure later

Give each container or app its own user-assigned identity (`id-rag-<service>`). In its settings, set
`AZURE_CLIENT_ID=<that identity's client ID>`, `KEY_VAULT_URL`, and `REQUIRE_AGENT_AUTH=true`.
`DefaultAzureCredential` then signs in as that identity, with no code changes, and use `https`
agent URLs.

## Troubleshooting

| Message | Fix |
|---|---|
| `… must not be set in .env … when KEY_VAULT_URL is set` | Delete that line from the `.env` file; the key comes from Key Vault now |
| `Key Vault secret '…' was not found` | Run `bash security/setup_azure.sh vault`, or check the vault name in `KEY_VAULT_URL` |
| `… may not read Key Vault secret …` (403) | Your account or identity lacks "Key Vault Secrets User". Re-run `setup_azure.sh team` / `identities`, then wait a few minutes |
| `Could not sign in to Key Vault` | Run `az login` (in the same terminal), or check `AZURE_CLIENT_ID` in Azure |
| `REQUIRE_AGENT_AUTH=true: no token for …` | That agent's token isn't in Key Vault, or `KEY_VAULT_URL` is missing from `orchestrator/.env` |
| An answer shows `AGENT_ACCESS_DENIED` / 401 | The orchestrator's and the agent's tokens differ. Restart both after rotating a token |

## Files

| File | Purpose |
|---|---|
| `service_secrets.py` | Key Vault loading, the "no keys in config" rule, and the startup check for agent tokens |
| `orchestrator/settings.py` | `REQUIRE_AGENT_AUTH` checks: a token for each agent, distinct tokens, https for remote agents |
| `orchestrator/main.py` | `/api/health` shows the auth status and where keys come from |
| `logs_agent/orchestrator_service.py`, `docs_agent/config.py` + `service.py`, `code_agent/service.py` + `config.py` | Each agent loads its own secrets and refuses to start without its token |
| `security/setup_azure.sh`, `security/azure.env.example` | Azure setup: vault, identities, roles |
| `security/verify_security.py` | Checks for the setup |
| `orchestrator/tests/test_security.py`, `test_verify_security.py` | Automated tests |
