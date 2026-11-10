"""Check the security setup and print PASS / WARN / FAIL for each rule.

    python security/verify_security.py                 # config files + Key Vault access (needs az login)
    python security/verify_security.py --live          # also call the running services (run_local.py)
    python security/verify_security.py --identities    # also list each managed identity's Azure roles

Run from the repository root with the project's virtual environment. Secret values are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service_secrets import AGENT_TOKEN_ENV, SERVICE_SECRETS  # noqa: E402

ENV_FILES = {"orchestrator": ROOT / "orchestrator" / ".env", "logs": ROOT / "logs_agent" / ".env",
             "docs": ROOT / "docs_agent" / ".env", "code": ROOT / "code_agent" / ".env"}
KEY_PATTERNS = [r"github_pat_[A-Za-z0-9_]{20,}", r"ghp_[A-Za-z0-9]{30,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
                r"AccountKey=[A-Za-z0-9+/=]{20,}", r"(API_KEY|AGENT_TOKEN|GITHUB_TOKEN)\s*=\s*['\"]?[A-Za-z0-9_\-]{20,}"]
EXPECTED_ROLES = {
    "orchestrator": {"Key Vault Secrets User", "Cognitive Services OpenAI User"},
    "logs": {"Key Vault Secrets User", "Azure AI User", "Log Analytics Reader"},
    "docs": {"Key Vault Secrets User", "Cognitive Services OpenAI User"},
    "code": {"Key Vault Secrets User", "Azure AI User", "Search Index Data Reader"},
}


class Report:
    def __init__(self):
        self.failed = 0

    def line(self, status: str, text: str):
        self.failed += status == "FAIL"
        print(f"  {status:<4} {text}")


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


# --------------------------------------------------------------------------- config files

def check_config(report: Report, env_files: dict[str, Path] = ENV_FILES):
    print("\n1. Keys are not in code or config files")
    tracked = [p for p in git("ls-files").stdout.splitlines()
               if re.search(r"(^|/)\.env(\..+)?$", p) and not p.endswith(".env.example")]
    report.line("FAIL" if tracked else "PASS",
                f".env files committed to git: {', '.join(tracked)}" if tracked else "no .env file is committed")
    for service, path in env_files.items():
        inside = path.is_relative_to(ROOT)
        rel = path.relative_to(ROOT) if inside else path
        if inside and path.exists() and git("check-ignore", "-q", str(path)).returncode != 0:
            report.line("FAIL", f"{rel} is not git-ignored")
        values = read_env(path)
        if not values:
            report.line("WARN", f"{rel}: not found (skipped)")
            continue
        secrets = [s.env for s in SERVICE_SECRETS[service] if values.get(s.env)]
        if values.get("KEY_VAULT_URL"):
            report.line("FAIL" if secrets else "PASS",
                        f"{rel}: Key Vault on, but {', '.join(secrets)} still in the file" if secrets
                        else f"{rel}: Key Vault on, no keys in the file")
        else:
            report.line("WARN", f"{rel}: KEY_VAULT_URL not set" + (f"; keys in file: {', '.join(secrets)}"
                                                                     if secrets else ""))
        if service != "orchestrator" or values.get("ORCHESTRATOR_MODE", "remote") == "remote":
            auth = values.get("REQUIRE_AGENT_AUTH", "").lower() in {"true", "1", "yes", "on"}
            report.line("PASS" if auth else "WARN", f"{rel}: REQUIRE_AGENT_AUTH={'true' if auth else 'not set'}")
    hits = git("grep", "-nIE", "|".join(KEY_PATTERNS), "--", ".", ":!*.example", ":!**/tests/**",
               ":!security/verify_security.py").stdout.strip().splitlines()
    report.line("FAIL" if hits else "PASS",
                "key-like strings in committed files: " + "; ".join(h.split(":", 2)[0] + ":" + h.split(":", 2)[1]
                                                                     for h in hits[:5])
                if hits else "no key-like strings in committed files")


# --------------------------------------------------------------------------- key vault

def check_key_vault(report: Report):
    print("\n2. Each service can read its own secrets from Key Vault")
    for service, path in ENV_FILES.items():
        values = read_env(path)
        if not values.get("KEY_VAULT_URL"):
            report.line("WARN", f"{service}: KEY_VAULT_URL not set in {path.relative_to(ROOT)} (skipped)")
            continue
        env = {k: v for k, v in os.environ.items() if k not in {s.env for s in SERVICE_SECRETS[service]}}
        env.update({k: v for k, v in values.items() if k not in {s.env for s in SERVICE_SECRETS[service]}})
        code = ("import json, service_secrets as s; "
                f"print(json.dumps(s.load_service_secrets({service!r})))")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
        if result.returncode == 0:
            loaded = json.loads(result.stdout.strip().splitlines()[-1])
            report.line("PASS", f"{service}: loaded {', '.join(f'{k} <- {v}' for k, v in loaded.items()) or 'nothing'}")
        else:
            message = (result.stderr.strip().splitlines() or ["unknown error"])[-1]
            report.line("FAIL", f"{service}: {message[:300]}")


# --------------------------------------------------------------------------- live services

def check_live(report: Report, orchestrator_url: str):
    import httpx
    print("\n3. The running services require the right token")
    values = read_env(ENV_FILES["orchestrator"])
    os.environ.update({k: v for k, v in values.items() if k not in os.environ})
    tokens = {}
    if values.get("KEY_VAULT_URL"):
        from service_secrets import load_service_secrets
        try:
            load_service_secrets("orchestrator")
        except Exception as exc:  # report, don't crash
            report.line("FAIL", f"could not load the orchestrator's tokens: {exc}")
    for agent, env in AGENT_TOKEN_ENV.items():
        tokens[agent] = os.getenv(env, "")
    body = {"request_id": "security-check", "question": "Security check", "task": "Security check",
            "intent": "unknown"}
    for agent, env in AGENT_TOKEN_ENV.items():
        url = values.get(f"{agent.upper()}_AGENT_URL")
        if not url:
            report.line("WARN", f"{agent}: no {agent.upper()}_AGENT_URL in orchestrator/.env (skipped)")
            continue
        try:
            anonymous = httpx.post(url, json=body, timeout=10).status_code
            wrong = httpx.post(url, json=body, timeout=10, headers={"Authorization": "Bearer not-the-token"}).status_code
            others = [a for a in tokens if a != agent and tokens[a]]
            cross = [httpx.post(url, json=body, timeout=10,
                                headers={"Authorization": f"Bearer {tokens[a]}"}).status_code for a in others]
        except httpx.HTTPError as exc:
            report.line("FAIL", f"{agent}: not reachable at {url} ({type(exc).__name__}); start run_local.py first")
            continue
        report.line("PASS" if anonymous == 401 else "FAIL", f"{agent}: call without a token -> {anonymous}")
        report.line("PASS" if wrong == 401 else "FAIL", f"{agent}: call with a wrong token -> {wrong}")
        if others:
            report.line("PASS" if all(c == 401 for c in cross) else "FAIL",
                        f"{agent}: call with another agent's token ({', '.join(others)}) -> {cross}")
    try:
        health = httpx.get(orchestrator_url.rstrip("/") + "/api/health", timeout=10).json()
    except httpx.HTTPError as exc:
        report.line("FAIL", f"orchestrator health not reachable at {orchestrator_url} ({type(exc).__name__})")
        return
    auth = health.get("agent_auth", {})
    report.line("PASS" if auth and all(auth.values()) else "FAIL", f"orchestrator sends a token to: {auth}")
    report.line("PASS" if health.get("require_agent_auth") else "WARN",
                f"orchestrator require_agent_auth={health.get('require_agent_auth')}")
    report.line("PASS" if health.get("secrets_source") == "key_vault" else "WARN",
                f"orchestrator secrets_source={health.get('secrets_source')}")


# --------------------------------------------------------------------------- identities

def check_identities(report: Report):
    print("\n4. Each service has its own identity with only the access it needs")
    az = shutil.which("az")
    config = read_env(ROOT / "security" / "azure.env")
    if not az or not config.get("RESOURCE_GROUP"):
        report.line("WARN", "needs the Azure CLI and security/azure.env (skipped)")
        return
    for service, expected in EXPECTED_ROLES.items():
        name = f"id-rag-{service}"
        shown = subprocess.run([az, "identity", "show", "-g", config["RESOURCE_GROUP"], "-n", name,
                                "--query", "principalId", "-o", "tsv"], capture_output=True, text=True)
        if shown.returncode != 0 or not shown.stdout.strip():
            report.line("FAIL", f"{name}: not found (run security/setup_azure.sh identities)")
            continue
        listed = subprocess.run([az, "role", "assignment", "list", "--assignee", shown.stdout.strip(), "--all",
                                 "--query", "[].roleDefinitionName", "-o", "json"], capture_output=True, text=True)
        roles = set(json.loads(listed.stdout or "[]"))
        missing, extra = expected - roles, roles - expected
        status = "FAIL" if missing else "WARN" if extra else "PASS"
        detail = f"roles: {', '.join(sorted(roles)) or 'none'}"
        if missing:
            detail += f"; missing: {', '.join(sorted(missing))}"
        if extra:
            detail += f"; more than needed: {', '.join(sorted(extra))}"
        report.line(status, f"{name}: {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="also call the running services")
    parser.add_argument("--orchestrator-url", default="http://127.0.0.1:8000")
    parser.add_argument("--identities", action="store_true", help="also check Azure role assignments")
    args = parser.parse_args(argv)
    report = Report()
    check_config(report)
    check_key_vault(report)
    if args.live:
        check_live(report, args.orchestrator_url)
    if args.identities:
        check_identities(report)
    print(f"\n{'All checks passed' if not report.failed else f'{report.failed} check(s) failed'}.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
