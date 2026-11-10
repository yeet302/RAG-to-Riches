"""Run the walkthrough against checked-in source files, with no external services."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not (root / "frontend/dist/index.html").exists():
        parser.error("Build the frontend first: cd frontend && npm run build")
    environment = os.environ.copy()
    for key in list(environment):
        if key.endswith("_AGENT_URL") or key.endswith("_AGENT_TOKEN") or key.startswith("AZURE_AI_"):
            environment.pop(key, None)
    environment.update(ORCHESTRATOR_MODE="local", ROUTING_MODE="keyword", ANSWER_MODE="evidence",
        SHOWCASE_OFFLINE="1", DOCS_ROOT=str(root / "orchestrator"),
        CODE_ROOT=str(root / "orchestrator"), LOGS_ROOT=str(root / "logs_agent/data"))
    print(f"Open http://127.0.0.1:{args.port} | Offline source search; no Azure calls.", flush=True)
    return subprocess.call([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
                            "--port", str(args.port)], cwd=root / "orchestrator", env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
