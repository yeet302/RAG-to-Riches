"""Start the configured local agent services and orchestrator with one command."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Service:
    name: str
    module: str
    cwd: Path
    env_file: Path
    host: str
    port: int
    mode: str = ""

    @property
    def base_url(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    @property
    def health_url(self):
        return self.base_url + ("/api/health" if self.name == "orchestrator" else "/health")

    def command(self):
        command = [sys.executable, "-m", "uvicorn", self.module, "--host", self.host, "--port", str(self.port)]
        if self.env_file.is_file():
            command += ["--env-file", str(self.env_file)]
        return command


def plan_services(env_file: Path, port: int, environ=None):
    values = {**dotenv_values(env_file), **(os.environ if environ is None else environ)}
    mode = values.get("ORCHESTRATOR_MODE", "remote").lower()
    if mode not in {"local", "remote"}:
        raise ValueError("ORCHESTRATOR_MODE must be local or remote")
    services, hosted = [], []
    specs = [("logs", "orchestrator_service:app", ROOT / "logs_agent"),
             ("docs", "docs_agent.service:app", ROOT),
             ("code", "code_agent.service:app", ROOT)]
    for role, module, cwd in specs:
        value = values.get(f"{role.upper()}_AGENT_URL") or ""
        if not value:
            if mode == "remote":
                raise ValueError(f"Set {role.upper()}_AGENT_URL in {env_file.name} before starting remote mode")
            continue
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"Invalid {role.upper()}_AGENT_URL; use a service URL without credentials")
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            hosted.append(role)
            continue
        if parsed.scheme != "http":
            raise ValueError(f"The local {role} launcher requires an http URL; HTTPS needs a separately hosted service")
        allowed_paths = {f"/agents/{role}/run"} | ({"/run"} if role != "docs" else set())
        if parsed.path.rstrip("/") not in allowed_paths:
            raise ValueError(f"The local {role} endpoint must use /agents/{role}/run or /run")
        host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
        services.append(Service(role, module, cwd, ROOT / f"{role}_agent" / ".env", host, parsed.port or 80))
    services.append(Service("orchestrator", "main:app", ROOT / "orchestrator", env_file, "127.0.0.1", port, mode))
    addresses = [(service.host, service.port) for service in services]
    if len(set(addresses)) != len(addresses):
        raise ValueError("Two services have the same local port; give each service its own port")
    return services, hosted


def port_is_open(service: Service):
    try:
        with socket.create_connection((service.host, service.port), timeout=0.3):
            return True
    except OSError:
        return False


def is_ready(service: Service):
    try:
        with build_opener(ProxyHandler({})).open(service.health_url, timeout=0.5) as response:
            body = json.loads(response.read(65536))
        if not isinstance(body, dict):
            return False
        if service.name == "orchestrator":
            return body.get("status") == "ok" and body.get("mode") == service.mode and isinstance(body.get("agents"), dict)
        return body.get("agent") == service.name
    except (OSError, ValueError):
        return False


def stop_started(processes):
    for name, process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for name, process in reversed(processes):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_services(services, hosted, log_dir: Path, timeout: float):
    started, handles, pending = [], [], []
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Inspect all ports before launching anything. Never replace an existing process.
        launch = []
        for service in services:
            if port_is_open(service):
                if not is_ready(service):
                    raise RuntimeError(f"Port {service.port} is occupied by a different or unhealthy service. Stop it or change the port.")
                print(f"[{service.name}] already running on port {service.port}; reusing it", flush=True)
            else:
                launch.append(service)
        for role in hosted:
            print(f"[{role}] configured hosted service; no local process needed", flush=True)
        for service in launch:
            log_path = log_dir / f"{service.name}.log"
            handle = log_path.open("w", encoding="utf-8")
            handles.append(handle)
            kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NO_WINDOW}
            process = subprocess.Popen(service.command(), cwd=service.cwd, env=os.environ.copy(),
                                       stdout=handle, stderr=subprocess.STDOUT, **kwargs)
            started.append((service.name, process))
            pending.append((service, process))
        deadline = time.monotonic() + timeout
        while pending:
            for service, process in pending[:]:
                if process.poll() is not None:
                    raise RuntimeError(f"{service.name} exited during startup. See {log_dir / (service.name + '.log')}")
                if is_ready(service):
                    pending.remove((service, process))
                    print(f"[{service.name}] ready on port {service.port}", flush=True)
            if pending and time.monotonic() >= deadline:
                names = ", ".join(service.name for service, _ in pending)
                raise RuntimeError(f"Startup timed out for {names}. See logs in {log_dir}")
            if pending:
                time.sleep(0.15)
        app = services[-1]
        print(f"\nOpen {app.base_url}\nService logs: {log_dir}\nPress Ctrl+C to stop services started by this command.", flush=True)
        if not (ROOT / "frontend/dist/index.html").exists():
            print("Frontend not built. Run npm run build from frontend/; API docs are available at /docs.", flush=True)
        print("Readiness confirms the HTTP services; Azure/source access is checked when you ask a question.", flush=True)
        while True:
            for name, process in started:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} stopped unexpectedly. See {log_dir / (name + '.log')}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping services started by this launcher...", flush=True)
        return 0
    finally:
        stop_started(started)
        for handle in handles:
            handle.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000, help="Orchestrator port (default: 8000)")
    parser.add_argument("--env-file", type=Path, default=ROOT / "orchestrator/.env")
    parser.add_argument("--startup-timeout", type=float, default=30)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or not 0 < args.startup_timeout < float("inf"):
        parser.error("Use a port between 1 and 65535 and a positive finite startup timeout")
    if not args.env_file.is_file():
        parser.error(f"Environment file not found: {args.env_file}")
    try:
        services, hosted = plan_services(args.env_file.resolve(), args.port)
        log_dir = ROOT / "diagnostics/local-run" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return run_services(services, hosted, log_dir, args.startup_timeout)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Launcher: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
