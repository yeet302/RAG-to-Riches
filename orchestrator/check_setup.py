"""Check that this computer can run main with all four services, before starting them.

Run from the repository root:
    python orchestrator/check_setup.py           # quick checks (no Azure calls)
    python orchestrator/check_setup.py --deep    # also builds the code agent the way its service does

It prints PASS / WARN / FAIL for each check and never prints secret values. The exit code is 1 if
anything FAILs. Most checks target the causes of "Code Agent is not configured" (CODE_NOT_CONFIGURED):
the code agent's service only builds the agent on the first question, so /health looks fine even
when its settings are wrong. See SETUP.md.
"""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PORTS = {"orchestrator": 8000, "logs": 8101, "docs": 8102, "code": 8103}
CODE_REQUIRED = ("FOUNDRY_PROJECT_ENDPOINT", "MODEL_DEPLOYMENT_NAME", "AZURE_SEARCH_ENDPOINT")
CODE_PACKAGES = ("azure.ai.projects", "azure.search.documents", "azure.identity", "openai", "requests")
MARKER_SUFFIXES = {".py", ".ts", ".tsx", ".json", ".md", ".css", ".txt", ".toml", ".cfg"}


class Report:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def add(self, status: str, message: str):
        self.lines.append((status, message))
        print(f"{status:<5} {message}", flush=True)

    @property
    def failed(self) -> bool:
        return any(status == "FAIL" for status, _ in self.lines)


def env_of(path: Path) -> dict[str, str]:
    return {k: (v or "").strip() for k, v in dotenv_values(path).items()} if path.is_file() else {}


def check_conflict_markers(report: Report, root: Path = ROOT):
    """Unresolved merge markers make Python files fail to import (the 4e36460 problem)."""
    try:
        listed = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
        files = [root / name for name in listed.splitlines()]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in root.rglob("*") if ".git" not in p.parts and "node_modules" not in p.parts]
    hits = []
    for path in files:
        if path.suffix not in MARKER_SUFFIXES or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if line.startswith(("<<<<<<< ", ">>>>>>> ")):
                hits.append(f"{path.relative_to(root)}:{number}")
    if hits:
        report.add("FAIL", "Unresolved merge conflict markers: " + ", ".join(hits[:6])
                   + ". Resolve them (or take main's version) before running.")
    else:
        report.add("PASS", "No merge conflict markers in tracked files.")


def check_orchestrator_env(report: Report, root: Path = ROOT) -> dict[str, str]:
    path = root / "orchestrator" / ".env"
    values = env_of(path)
    if not values:
        report.add("FAIL", "orchestrator/.env is missing or empty. Copy orchestrator/.env.example and fill it in.")
        return values
    mode = values.get("ORCHESTRATOR_MODE", "remote").lower()
    if mode == "remote":
        missing = [f"{n}_AGENT_URL" for n in ("LOGS", "DOCS", "CODE") if not values.get(f"{n}_AGENT_URL")]
        if missing:
            report.add("FAIL", f"orchestrator/.env: remote mode needs {', '.join(missing)}.")
        else:
            report.add("PASS", "orchestrator/.env: remote mode with all three agent URLs.")
    else:
        report.add("WARN", f"orchestrator/.env: ORCHESTRATOR_MODE={mode}; the live agents are not used.")
    if values.get("AZURE_AI_ENDPOINT") and values.get("AZURE_AI_MODEL"):
        auth = values.get("AZURE_AI_AUTH_MODE", "api_key").lower()
        if auth == "entra" or values.get("AZURE_AI_API_KEY"):
            report.add("PASS", f"orchestrator/.env: AI planning and answers configured ({auth}).")
        else:
            report.add("FAIL", "orchestrator/.env: AZURE_AI_API_KEY is empty; set it or AZURE_AI_AUTH_MODE=entra.")
    else:
        report.add("WARN", "orchestrator/.env: AZURE_AI_ENDPOINT / AZURE_AI_MODEL not set; keyword routing and "
                           "evidence-only answers will be used.")
    return values


def check_code_agent_env(report: Report, root: Path = ROOT):
    """The settings code_agent/config.py Settings.load() requires; missing ones mean CODE_NOT_CONFIGURED."""
    path = root / "code_agent" / ".env"
    values = {**env_of(root / ".env"), **env_of(path)}   # Settings.load() also reads a .env in the repo root
    if not path.is_file():
        report.add("FAIL", "code_agent/.env is missing. Copy code_agent/.env.example and fill it in.")
    missing = [key for key in CODE_REQUIRED if not values.get(key)]
    if missing:
        report.add("FAIL", f"code_agent/.env: missing {', '.join(missing)} -> the code agent will answer "
                           "CODE_NOT_CONFIGURED.")
    elif path.is_file():
        report.add("PASS", "code_agent/.env: Foundry project, model deployment and Search endpoint set.")
    approved = approved_repository(root)
    repo = values.get("CODE_REPO") or approved
    if approved and repo != approved:
        report.add("FAIL", f"code_agent/.env: CODE_REPO={repo}, but only {approved} is approved -> "
                           f"CODE_NOT_CONFIGURED. Set CODE_REPO={approved} (or delete the line).")
    elif approved:
        report.add("PASS", f"code_agent/.env: repository {approved}.")
    if values.get("CODE_REF"):
        report.add("PASS", f"code_agent/.env: CODE_REF pinned to {values['CODE_REF'][:12]}.")
    else:
        report.add("WARN", "code_agent/.env: CODE_REF is empty, so the default branch is used. Set it to the "
                           "commit the search index was built from (ask Navya), or searches may find nothing.")


def approved_repository(root: Path = ROOT) -> str:
    config = root / "code_agent" / "config.py"
    if not config.is_file():
        return ""
    for line in config.read_text(encoding="utf-8").splitlines():
        if line.startswith("APPROVED_REPOSITORY"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def check_other_agents(report: Report, root: Path = ROOT):
    logs = env_of(root / "logs_agent" / ".env")
    if not logs:
        report.add("FAIL", "logs_agent/.env is missing or empty. Copy logs_agent/.env.example and fill it in.")
    else:
        missing = [k for k in ("FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_AGENT_NAME") if not logs.get(k)]
        if logs.get("LOG_CONNECTOR", "local").lower() == "azure" and not logs.get("AZURE_LOG_ANALYTICS_WORKSPACE_ID"):
            missing.append("AZURE_LOG_ANALYTICS_WORKSPACE_ID")
        if "AZURE_AI_ENDPOINT" in logs or "LOGS_AGENT_URL" in logs:
            report.add("WARN", "logs_agent/.env looks like an orchestrator file (it has AZURE_AI_ENDPOINT or "
                               "LOGS_AGENT_URL). Use logs_agent/.env.example.")
        report.add("FAIL" if missing else "PASS",
                   f"logs_agent/.env: missing {', '.join(missing)}." if missing else "logs_agent/.env: configured.")
    docs = env_of(root / "docs_agent" / ".env")
    if not docs:
        report.add("FAIL", "docs_agent/.env is missing or empty. Copy docs_agent/.env.example and fill it in.")
    else:
        endpoint = docs.get("AZURE_AI_ENDPOINT") or docs.get("AZURE_OPENAI_ENDPOINT")
        model = docs.get("AZURE_AI_MODEL") or docs.get("AZURE_OPENAI_DEPLOYMENT")
        key = docs.get("AZURE_AI_API_KEY") or docs.get("AZURE_OPENAI_API_KEY")
        entra = docs.get("AZURE_AI_AUTH_MODE", "api_key").lower() == "entra"
        if endpoint and model and (key or entra):
            report.add("PASS", "docs_agent/.env: model endpoint, deployment and credentials set.")
        else:
            report.add("WARN", "docs_agent/.env: endpoint, deployment or key missing; the docs agent will return "
                               "excerpts without AI analysis.")


def check_packages(report: Report):
    missing = []
    for name in CODE_PACKAGES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        report.add("FAIL", f"Python packages missing: {', '.join(missing)}. Run: python -m pip install -r "
                           "orchestrator/requirements-dev.txt")
    else:
        report.add("PASS", "Code agent Python packages are installed.")


def check_azure_cli(report: Report):
    az = shutil.which("az")
    if not az:
        report.add("FAIL", "The Azure CLI (az) is not on PATH in this terminal. Windows: "
                           '$env:Path += ";C:\\Program Files\\Microsoft SDKs\\Azure\\CLI2\\wbin"')
        return
    signed_in = subprocess.run([az, "account", "show", "-o", "none"], capture_output=True, text=True)
    if signed_in.returncode:
        report.add("FAIL", "Not signed in to Azure in this terminal. Run: az login")
    else:
        report.add("PASS", "Azure CLI found and signed in (the agents use this sign-in).")


def check_ports(report: Report):
    busy = []
    for name, port in PORTS.items():
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(f"{name} ({port})")
    if busy:
        report.add("WARN", "Already running: " + ", ".join(busy) + ". run_local.py reuses running services, so "
                           "after pulling new code stop them first (see SETUP.md).")
    else:
        report.add("PASS", "Ports 8000 and 8101-8103 are free.")


def deep_code_agent(report: Report, root: Path = ROOT):
    """Build CodeAgent() exactly as code_agent/service.py does on the first question."""
    saved = dict(os.environ)
    try:
        os.environ.update({k: v for k, v in env_of(root / "code_agent" / ".env").items() if v})
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from code_agent.agent import CodeAgent
        CodeAgent()
        report.add("PASS", "Code agent builds (Foundry project, Search client and settings accepted).")
    except Exception as exc:   # the same failure the service reports as CODE_NOT_CONFIGURED
        report.add("FAIL", f"Code agent does not build: {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        os.environ.clear()
        os.environ.update(saved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deep", action="store_true", help="also build the code agent the way its service does")
    args = parser.parse_args(argv)
    report = Report()
    check_conflict_markers(report)
    check_orchestrator_env(report)
    check_code_agent_env(report)
    check_other_agents(report)
    check_packages(report)
    check_azure_cli(report)
    check_ports(report)
    if args.deep:
        deep_code_agent(report)
    print("\nResult: " + ("FAIL - fix the lines marked FAIL, then run this again." if report.failed
                          else "OK - start the services with: python orchestrator/run_local.py"))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
