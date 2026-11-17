"""check_setup.py: the setup checks that catch "Code Agent is not configured" before the services start.

Each test builds a fake repository folder (tmp_path) with only the files that check needs, runs one
check, and looks at the PASS / WARN / FAIL lines it reported. Nothing calls Azure.

Run: python -m pytest tests/test_check_setup.py -v      (from the orchestrator folder)
"""
from __future__ import annotations

import check_setup as cs

CONFIG = 'APPROVED_REPOSITORY = "yeet302/RAG-to-Riches"\n'
GOOD_CODE_ENV = ("FOUNDRY_PROJECT_ENDPOINT=https://x.services.ai.azure.com/api/projects/p\n"
                 "MODEL_DEPLOYMENT_NAME=gpt-4.1-mini\nAZURE_SEARCH_ENDPOINT=https://s.search.windows.net/\n"
                 "CODE_REPO=yeet302/RAG-to-Riches\nCODE_REF=4a701124503f86431f52a000c01d2d73322d39e0\n")


def repo(tmp_path, code_env=None):
    (tmp_path / "code_agent").mkdir()
    (tmp_path / "code_agent" / "config.py").write_text(CONFIG)
    if code_env is not None:
        (tmp_path / "code_agent" / ".env").write_text(code_env)
    return tmp_path


def statuses(report, text):
    return [status for status, message in report.lines if text in message]


def test_complete_code_agent_settings_pass(tmp_path):
    """All required code agent settings, the approved repo and a pinned commit: no FAIL or WARN."""
    report = cs.Report()
    cs.check_code_agent_env(report, repo(tmp_path, GOOD_CODE_ENV))
    assert not report.failed and all(status == "PASS" for status, _ in report.lines)


def test_missing_code_agent_settings_fail_and_name_the_symptom(tmp_path):
    """A missing Search endpoint is reported as the cause of CODE_NOT_CONFIGURED."""
    report = cs.Report()
    cs.check_code_agent_env(report, repo(tmp_path, GOOD_CODE_ENV.replace("AZURE_SEARCH_ENDPOINT", "#X")))
    assert report.failed and statuses(report, "missing AZURE_SEARCH_ENDPOINT -> the code agent will answer "
                                              "CODE_NOT_CONFIGURED") == ["FAIL"]


def test_missing_code_agent_env_file_fails(tmp_path):
    """No code_agent/.env at all is a FAIL with the file to copy."""
    report = cs.Report()
    cs.check_code_agent_env(report, repo(tmp_path))
    assert statuses(report, "code_agent/.env is missing") == ["FAIL"]


def test_unapproved_repository_fails(tmp_path):
    """The old CODE_REPO value (open-telemetry/opentelemetry-demo) is refused by config.py, so it FAILs here."""
    report = cs.Report()
    env = GOOD_CODE_ENV.replace("CODE_REPO=yeet302/RAG-to-Riches", "CODE_REPO=open-telemetry/opentelemetry-demo")
    cs.check_code_agent_env(report, repo(tmp_path, env))
    assert statuses(report, "only yeet302/RAG-to-Riches is approved") == ["FAIL"]
    assert "Set CODE_REPO=yeet302/RAG-to-Riches" in " ".join(m for _, m in report.lines)


def test_unpinned_commit_is_a_warning(tmp_path):
    """An empty CODE_REF still runs, so it is a WARN, not a FAIL."""
    report = cs.Report()
    cs.check_code_agent_env(report, repo(tmp_path, GOOD_CODE_ENV.replace("CODE_REF=", "#CODE_REF=")))
    assert not report.failed and statuses(report, "CODE_REF is empty") == ["WARN"]


def test_conflict_markers_are_found(tmp_path):
    """A committed merge conflict (like 4e36460 in code_agent/agent.py) is a FAIL that names the file and line."""
    (tmp_path / "agent.py").write_text("x = 1\n<<<<<<< HEAD\ny = 2\n=======\ny = 3\n>>>>>>> main\n")
    report = cs.Report()
    cs.check_conflict_markers(report, tmp_path)
    assert report.failed and "agent.py:2" in report.lines[0][1] and "agent.py:6" in report.lines[0][1]


def test_logs_env_with_orchestrator_settings_is_flagged(tmp_path):
    """logs_agent/.env holding the orchestrator's settings (a mix-up that happened) gives a WARN and a FAIL."""
    (tmp_path / "logs_agent").mkdir()
    (tmp_path / "logs_agent" / ".env").write_text("AZURE_AI_ENDPOINT=https://x\nLOGS_AGENT_URL=http://127.0.0.1:8101\n")
    report = cs.Report()
    cs.check_other_agents(report, tmp_path)
    assert statuses(report, "looks like an orchestrator file") == ["WARN"]
    assert statuses(report, "logs_agent/.env: missing FOUNDRY_PROJECT_ENDPOINT") == ["FAIL"]


def test_remote_mode_needs_all_three_agent_urls(tmp_path):
    """Remote mode without CODE_AGENT_URL is a FAIL naming the missing setting."""
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "orchestrator" / ".env").write_text(
        "ORCHESTRATOR_MODE=remote\nLOGS_AGENT_URL=http://127.0.0.1:8101/agents/logs/run\n"
        "DOCS_AGENT_URL=http://127.0.0.1:8102/agents/docs/run\n")
    report = cs.Report()
    cs.check_orchestrator_env(report, tmp_path)
    assert statuses(report, "remote mode needs CODE_AGENT_URL") == ["FAIL"]


def test_this_repository_has_no_conflict_markers():
    """The real repository: no unresolved merge markers anywhere in tracked files."""
    report = cs.Report()
    cs.check_conflict_markers(report)
    assert not report.failed, report.lines
