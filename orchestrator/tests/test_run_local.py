"""Configuration boundaries and ownership-safe launcher cleanup."""
import subprocess
import sys

import pytest

from run_local import ROOT, plan_services, stop_started


def env_file(tmp_path, **values):
    path = tmp_path / ".env"
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()))
    return path


def test_configured_ports_and_separate_env_files_are_preserved(tmp_path):
    path = env_file(tmp_path, ORCHESTRATOR_MODE="remote",
        LOGS_AGENT_URL="http://127.0.0.1:18101/agents/logs/run",
        DOCS_AGENT_URL="http://localhost:18102/agents/docs/run",
        CODE_AGENT_URL="http://127.0.0.1:18103/agents/code/run")
    services, hosted = plan_services(path, 18000, environ={})
    assert not hosted
    assert [service.port for service in services] == [18101, 18102, 18103, 18000]
    assert services[0].cwd == ROOT / "logs_agent"
    assert [service.env_file for service in services[:3]] == [ROOT / (name + "_agent") / ".env" for name in ["logs", "docs", "code"]]
    assert services[-1].env_file == path
    assert services[1].host == "127.0.0.1"


def test_hosted_agents_are_not_started_locally(tmp_path):
    path = env_file(tmp_path, ORCHESTRATOR_MODE="remote", **{
        role + "_AGENT_URL": "https://example.invalid/agents/" + role.lower() + "/run"
        for role in ["LOGS", "DOCS", "CODE"]})
    services, hosted = plan_services(path, 8000, environ={})
    assert [service.name for service in services] == ["orchestrator"]
    assert hosted == ["logs", "docs", "code"]


def test_local_file_mode_needs_only_orchestrator(tmp_path):
    services, hosted = plan_services(env_file(tmp_path, ORCHESTRATOR_MODE="local"), 8000, environ={})
    assert len(services) == 1 and services[0].mode == "local" and not hosted


def test_shell_settings_override_env_file(tmp_path):
    path = env_file(tmp_path, ORCHESTRATOR_MODE="remote")
    services, _ = plan_services(path, 8000, environ={"ORCHESTRATOR_MODE":"local"})
    assert len(services) == 1 and services[0].mode == "local"


def test_missing_endpoint_is_actionable(tmp_path):
    with pytest.raises(ValueError, match="LOGS_AGENT_URL"):
        plan_services(env_file(tmp_path, ORCHESTRATOR_MODE="remote"), 8000, environ={})


def test_duplicate_ports_are_rejected_before_startup(tmp_path):
    path = env_file(tmp_path, ORCHESTRATOR_MODE="local", CODE_AGENT_URL="http://127.0.0.1:8000/agents/code/run")
    with pytest.raises(ValueError, match="same local port"):
        plan_services(path, 8000, environ={})


def test_cleanup_stops_only_owned_processes():
    owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    existing = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        stop_started([("owned", owned)])
        assert owned.poll() is not None
        assert existing.poll() is None
    finally:
        existing.terminate()
        existing.wait(timeout=5)
        if owned.poll() is None:
            owned.terminate()
            owned.wait(timeout=5)
