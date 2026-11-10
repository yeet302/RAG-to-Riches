"""Keys from Key Vault only, each service reads only its own secrets, and agent calls are authenticated."""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceNotFoundError
from fastapi.testclient import TestClient

from settings import Settings  # first: puts the repo root (service_secrets) on sys.path
import service_secrets as ss  # noqa: E402

VAULT = "https://kv-rag-to-riches.vault.azure.net/"
SECRETS = {
    "orchestrator-azure-ai-api-key": "orch-key", "docs-azure-ai-api-key": "docs-key",
    "code-github-token": "github-pat", "logs-agent-token": "tok-logs", "docs-agent-token": "tok-docs",
    "code-agent-token": "tok-code",
}
ALL_ENV = ["KEY_VAULT_URL", "AZURE_AI_API_KEY", "GITHUB_TOKEN", "REQUIRE_AGENT_AUTH",
           "LOGS_AGENT_TOKEN", "DOCS_AGENT_TOKEN", "CODE_AGENT_TOKEN",
           "LOGS_AGENT_URL", "DOCS_AGENT_URL", "CODE_AGENT_URL", "KEY_VAULT_SECRET_GITHUB_TOKEN"]


class FakeVault:
    """Records which secrets a service asked for, like Key Vault's audit log."""
    def __init__(self, secrets=None, forbidden=()):
        self.secrets, self.forbidden, self.requested = dict(SECRETS if secrets is None else secrets), set(forbidden), []

    def get_secret(self, name):
        self.requested.append(name)
        if name in self.forbidden:
            error = HttpResponseError(message="Forbidden")
            error.status_code = 403
            raise error
        if name not in self.secrets:
            raise ResourceNotFoundError(message="SecretNotFound")
        return SimpleNamespace(value=self.secrets[name])


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    ss.reset_for_tests()
    yield
    ss.reset_for_tests()


def vault_on(monkeypatch, **env):
    monkeypatch.setenv("KEY_VAULT_URL", VAULT)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


# ---- Key Vault -----------------------------------------------------------------------------------

def test_without_a_key_vault_nothing_changes():
    assert ss.load_service_secrets("docs", client_factory=lambda url: pytest.fail("no vault call")) == {}
    assert ss.secrets_source() == "environment"


@pytest.mark.parametrize("service, expected", [
    ("logs", ["logs-agent-token"]),
    ("docs", ["docs-agent-token", "docs-azure-ai-api-key"]),
    ("code", ["code-agent-token", "code-github-token"]),
])
def test_each_agent_reads_only_its_own_secrets(monkeypatch, service, expected):
    vault_on(monkeypatch)
    vault = FakeVault()
    loaded = ss.load_service_secrets(service, client=vault)
    assert vault.requested == expected
    assert sorted(loaded.values()) == sorted(expected)


def test_orchestrator_reads_the_tokens_of_the_agents_it_calls(monkeypatch):
    vault_on(monkeypatch, LOGS_AGENT_URL="http://127.0.0.1:8101/agents/logs/run",
             CODE_AGENT_URL="http://127.0.0.1:8103/agents/code/run")
    vault = FakeVault()
    ss.load_service_secrets("orchestrator", client=vault)
    assert vault.requested == ["orchestrator-azure-ai-api-key", "logs-agent-token", "code-agent-token"]
    import os
    assert os.environ["LOGS_AGENT_TOKEN"] == "tok-logs" and os.environ["AZURE_AI_API_KEY"] == "orch-key"
    assert "DOCS_AGENT_TOKEN" not in os.environ


def test_keys_left_in_a_config_file_are_refused(monkeypatch):
    vault_on(monkeypatch, GITHUB_TOKEN="ghp_left_in_env")
    with pytest.raises(ss.SecretsError, match="GITHUB_TOKEN must not be set in .env"):
        ss.load_service_secrets("code", client=FakeVault())


def test_optional_keys_may_be_absent_for_keyless_sign_in(monkeypatch):
    vault_on(monkeypatch)
    secrets = {k: v for k, v in SECRETS.items() if k != "docs-azure-ai-api-key"}
    assert ss.load_service_secrets("docs", client=FakeVault(secrets)) == {"DOCS_AGENT_TOKEN": "docs-agent-token"}


def test_a_missing_agent_token_stops_startup(monkeypatch):
    vault_on(monkeypatch)
    with pytest.raises(ss.SecretsError, match="'logs-agent-token' .* was not found"):
        ss.load_service_secrets("logs", client=FakeVault({}))


def test_no_permission_names_the_role_to_grant(monkeypatch):
    vault_on(monkeypatch)
    with pytest.raises(ss.SecretsError, match="Key Vault Secrets User"):
        ss.load_service_secrets("code", client=FakeVault(forbidden={"code-github-token"}))


def test_not_signed_in_says_how_to_sign_in(monkeypatch):
    vault_on(monkeypatch)

    class NoSignIn:
        def get_secret(self, name):
            raise ClientAuthenticationError(message="no credential")
    with pytest.raises(ss.SecretsError, match="az login"):
        ss.load_service_secrets("logs", client=NoSignIn())


def test_secret_names_can_be_overridden(monkeypatch):
    vault_on(monkeypatch, KEY_VAULT_SECRET_GITHUB_TOKEN="team-github")
    vault = FakeVault({**SECRETS, "team-github": "pat"})
    ss.load_service_secrets("code", client=vault)
    assert "team-github" in vault.requested


def test_vault_url_must_be_a_key_vault(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URL", "http://example.com")
    with pytest.raises(ss.SecretsError, match="vault.azure.net"):
        ss.load_service_secrets("logs", client=FakeVault())


def test_secret_values_never_appear_in_logs_or_return_values(monkeypatch, caplog):
    vault_on(monkeypatch)
    caplog.set_level("INFO")
    loaded = ss.load_service_secrets("code", client=FakeVault())
    assert "tok-code" not in caplog.text and "github-pat" not in caplog.text
    assert "tok-code" not in str(loaded)


def test_orchestrator_settings_load_from_key_vault(monkeypatch):
    vault_on(monkeypatch, DOCS_AGENT_URL="http://127.0.0.1:8102/agents/docs/run")
    vault = FakeVault()
    monkeypatch.setattr(ss, "_default_client", lambda url: vault)
    settings = Settings.from_env()
    assert settings.agent_tokens == {"docs": "tok-docs"} and settings.llm_api_key == "orch-key"


# ---- enforced authentication ---------------------------------------------------------------------

URLS = {n: f"http://127.0.0.1:810{i}/agents/{n}/run" for i, n in enumerate(("logs", "docs", "code"), 1)}


def test_required_auth_needs_a_token_for_every_agent():
    with pytest.raises(ValueError, match="no token for code"):
        Settings(agent_urls=URLS, agent_tokens={"logs": "a", "docs": "b"}, require_agent_auth=True)


def test_required_auth_needs_a_different_token_per_agent():
    with pytest.raises(ValueError, match="its own token"):
        Settings(agent_urls=URLS, agent_tokens={"logs": "same", "docs": "same", "code": "c"},
                 require_agent_auth=True)


def test_required_auth_needs_https_for_agents_on_other_machines():
    urls = {**URLS, "logs": "http://10.0.0.5:8101/agents/logs/run"}
    with pytest.raises(ValueError, match="use https"):
        Settings(agent_urls=urls, agent_tokens={"logs": "a", "docs": "b", "code": "c"}, require_agent_auth=True)
    https = {**URLS, "logs": "https://logs.example.net/agents/logs/run"}
    Settings(agent_urls=https, agent_tokens={"logs": "a", "docs": "b", "code": "c"}, require_agent_auth=True)


def test_auth_stays_optional_unless_required():
    Settings(agent_urls=URLS)   # existing local setups keep working


def test_agents_refuse_to_start_without_a_token_when_auth_is_required(monkeypatch):
    monkeypatch.setenv("REQUIRE_AGENT_AUTH", "true")
    for agent in ("logs", "docs", "code"):
        with pytest.raises(ss.SecretsError, match="REQUIRE_AGENT_AUTH=true"):
            ss.require_agent_token(agent)
    monkeypatch.setenv("CODE_AGENT_TOKEN", "x")
    ss.require_agent_token("code")
    from code_agent.service import create_app
    monkeypatch.delenv("CODE_AGENT_TOKEN")
    with pytest.raises(ss.SecretsError):
        create_app()


def _agent_clients(monkeypatch):
    """The three real agent services, each with its own token."""
    monkeypatch.setenv("LOGS_AGENT_TOKEN", "tok-logs")
    monkeypatch.setenv("DOCS_AGENT_TOKEN", "tok-docs")
    monkeypatch.setenv("CODE_AGENT_TOKEN", "tok-code")
    monkeypatch.setenv("REQUIRE_AGENT_AUTH", "true")
    from code_agent.service import create_app as code_app
    from docs_agent.service import create_app as docs_app
    from docs_agent.tests.support import offline_agent
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "logs_agent"))
    logs = importlib.reload(importlib.import_module("orchestrator_service"))
    monkeypatch.setattr(logs, "handle_agent_request", lambda request: logs.AgentFinding(
        agent="logs", status="no_evidence", summary="none"))
    return {"logs": TestClient(logs.app), "docs": TestClient(docs_app(offline_agent(token="tok-docs"))),
            "code": TestClient(code_app())}


REQUEST = {"request_id": "r1", "question": "Why?", "task": "Search.", "intent": "incident"}


@pytest.mark.parametrize("agent", ["logs", "docs", "code"])
def test_each_agent_rejects_missing_and_other_agents_tokens(monkeypatch, agent):
    client = _agent_clients(monkeypatch)[agent]
    path = f"/agents/{agent}/run"
    assert client.post(path, json=REQUEST).status_code == 401
    for other in {"logs", "docs", "code"} - {agent}:
        assert client.post(path, json=REQUEST, headers={"Authorization": f"Bearer tok-{other}"}).status_code == 401
    assert client.post(path, json=REQUEST, headers={"Authorization": f"Bearer tok-{agent}"}).status_code == 200


def test_health_reports_auth_and_where_keys_come_from_without_values():
    import main
    with TestClient(main.app) as client:
        body = client.get("/api/health").json()
    assert set(body["agent_auth"]) == {"logs", "docs", "code"}
    assert body["secrets_source"] in {"environment", "key_vault"} and "require_agent_auth" in body
    assert "tok-" not in str(body)
