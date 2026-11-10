"""API keys and agent tokens from Azure Key Vault, and enforced agent authentication.

Every service (orchestrator, logs, docs, code) calls ``load_service_secrets(<service>)`` once at
startup, before it reads its settings:

* ``KEY_VAULT_URL`` unset: nothing happens, so local development keeps working.
* ``KEY_VAULT_URL`` set: each secret this service uses is read from Key Vault with the service's
  own Azure identity (``DefaultAzureCredential``: your ``az login`` on a laptop, the service's
  managed identity in Azure, chosen with ``AZURE_CLIENT_ID``). A key that is *also* present in a
  ``.env`` file or the environment is an error, so keys can't quietly stay in config files.

Each service reads only its own secrets (table below). In Azure, each service's identity is given
"Key Vault Secrets User" on exactly those secrets, so the docs agent cannot read the GitHub token,
the logs agent cannot read the docs key, and so on. See SECURITY_SETUP.md.

``REQUIRE_AGENT_AUTH=true`` makes authentication mandatory: an agent refuses to start without its
token, and the orchestrator refuses to start unless every configured agent has its own token.
Secret values are never logged or returned.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

log = logging.getLogger("service_secrets")


@dataclass(frozen=True)
class SecretSpec:
    env: str                 # environment variable the service reads
    secret: str              # Key Vault secret name
    required: bool           # must exist in the vault (optional secrets, e.g. API keys, may be absent)
    when_env: str = ""       # only needed when this variable is set (e.g. the agent's URL)


# Which secrets each service may read. Keep in sync with security/setup_azure.sh.
SERVICE_SECRETS: dict[str, tuple[SecretSpec, ...]] = {
    "orchestrator": (
        SecretSpec("AZURE_AI_API_KEY", "orchestrator-azure-ai-api-key", required=False),
        SecretSpec("LOGS_AGENT_TOKEN", "logs-agent-token", required=True, when_env="LOGS_AGENT_URL"),
        SecretSpec("DOCS_AGENT_TOKEN", "docs-agent-token", required=True, when_env="DOCS_AGENT_URL"),
        SecretSpec("CODE_AGENT_TOKEN", "code-agent-token", required=True, when_env="CODE_AGENT_URL"),
    ),
    "logs": (
        SecretSpec("LOGS_AGENT_TOKEN", "logs-agent-token", required=True),
    ),
    "docs": (
        SecretSpec("DOCS_AGENT_TOKEN", "docs-agent-token", required=True),
        SecretSpec("AZURE_AI_API_KEY", "docs-azure-ai-api-key", required=False),
    ),
    "code": (
        SecretSpec("CODE_AGENT_TOKEN", "code-agent-token", required=True),
        SecretSpec("GITHUB_TOKEN", "code-github-token", required=False),
    ),
}
AGENT_TOKEN_ENV = {"logs": "LOGS_AGENT_TOKEN", "docs": "DOCS_AGENT_TOKEN", "code": "CODE_AGENT_TOKEN"}


class SecretsError(RuntimeError):
    """Startup must stop: a secret is missing, unreadable, or still in a config file."""


class _Client(Protocol):
    def get_secret(self, name: str): ...


_loaded: dict[str, dict[str, str]] = {}


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "true" if default else "false").strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off", ""}:
        return False
    raise SecretsError(f"{name} must be true or false")


def _default_client(vault_url: str) -> _Client:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    return SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())


def _secret_name(spec: SecretSpec) -> str:
    """A team can rename a secret without code changes: KEY_VAULT_SECRET_GITHUB_TOKEN=my-name."""
    return os.getenv(f"KEY_VAULT_SECRET_{spec.env}", spec.secret).strip()


def load_service_secrets(service: str, client: Optional[_Client] = None,
                         client_factory: Optional[Callable[[str], _Client]] = None) -> dict[str, str]:
    """Put this service's secrets from Key Vault into os.environ. Returns {env var: secret name}
    for what was loaded (never values). Safe to call more than once."""
    if service not in SERVICE_SECRETS:
        raise SecretsError(f"unknown service '{service}'")
    vault_url = os.getenv("KEY_VAULT_URL", "").strip()
    if not vault_url:
        return {}
    if service in _loaded and client is None:
        return dict(_loaded[service])
    if not vault_url.startswith("https://") or ".vault." not in vault_url:
        raise SecretsError("KEY_VAULT_URL must look like https://<name>.vault.azure.net/")

    specs = [s for s in SERVICE_SECRETS[service] if not s.when_env or os.getenv(s.when_env)]
    in_config = [s.env for s in specs if os.getenv(s.env)]
    if in_config:
        raise SecretsError(
            f"{', '.join(in_config)} must not be set in .env or the environment when KEY_VAULT_URL is set; "
            "keys are read only from Key Vault. Remove the line(s) and restart.")

    from azure.core.exceptions import (AzureError, ClientAuthenticationError, HttpResponseError,
                                       ResourceNotFoundError)
    client = client or (client_factory or _default_client)(vault_url)
    loaded: dict[str, str] = {}
    for spec in specs:
        name = _secret_name(spec)
        try:
            value = client.get_secret(name).value or ""
        except ResourceNotFoundError:
            if spec.required:
                raise SecretsError(f"Key Vault secret '{name}' (for {spec.env}) was not found in {vault_url}. "
                                   "Create it with security/setup_azure.sh.") from None
            continue
        except ClientAuthenticationError as exc:
            raise SecretsError(f"Could not sign in to Key Vault for {service}: run `az login` (locally) or "
                               "check the service's managed identity / AZURE_CLIENT_ID.") from exc
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) == 403:
                raise SecretsError(f"The {service} identity may not read Key Vault secret '{name}'. Give it "
                                   "'Key Vault Secrets User' on that secret (security/setup_azure.sh).") from None
            raise SecretsError(f"Key Vault returned an error for secret '{name}': "
                               f"{getattr(exc, 'status_code', '?')}") from None
        except AzureError as exc:
            raise SecretsError(f"Could not reach Key Vault {vault_url} ({type(exc).__name__}). Check the "
                               "URL and your network.") from None
        if not value.strip():
            if spec.required:
                raise SecretsError(f"Key Vault secret '{name}' (for {spec.env}) is empty.")
            continue
        os.environ[spec.env] = value.strip()
        loaded[spec.env] = name
    _loaded[service] = loaded
    log.info("service=%s loaded %d secret(s) from Key Vault: %s", service, len(loaded),
             ", ".join(f"{env}<-{name}" for env, name in loaded.items()) or "none")
    return dict(loaded)


def secrets_source() -> str:
    return "key_vault" if os.getenv("KEY_VAULT_URL", "").strip() else "environment"


def require_agent_token(agent: str) -> None:
    """With REQUIRE_AGENT_AUTH=true an agent must not start without its token (fail closed)."""
    env = AGENT_TOKEN_ENV[agent]
    if env_flag("REQUIRE_AGENT_AUTH") and not os.getenv(env, "").strip():
        raise SecretsError(f"REQUIRE_AGENT_AUTH=true but {env} is empty: the {agent} agent would accept "
                           "unauthenticated calls. Set KEY_VAULT_URL (or the token) and restart.")


def reset_for_tests() -> None:
    _loaded.clear()
