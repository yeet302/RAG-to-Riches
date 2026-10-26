"""Server-side configuration; never accept data roots or agent URLs from chat input."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
NAMES = ("logs", "docs", "code")  # "code" is the wire name for the codebase agent.


@dataclass(frozen=True)
class Settings:
    mode: str = "local"
    timeout_seconds: float = 20.0
    docs_root: Path = ROOT
    code_root: Path = ROOT
    logs_root: Path | None = None
    agent_urls: dict[str, str] = field(default_factory=dict)
    agent_tokens: dict[str, str] = field(default_factory=dict, repr=False)
    routing_mode: str = "auto"
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_api_style: str = "openai_v1"
    llm_api_version: str = ""
    llm_timeout_seconds: float = 10.0
    answer_mode: str = "auto"
    synthesis_timeout_seconds: float = 30.0

    @property
    def effective_routing_mode(self) -> str:
        if self.routing_mode == "auto":
            return "llm" if self.llm_configured and self.mode != "demo" else "keyword"
        return self.routing_mode

    @property
    def effective_answer_mode(self) -> str:
        if self.mode == "demo":
            return "evidence"
        if self.answer_mode == "auto":
            return "llm" if self.llm_configured else "evidence"
        return self.answer_mode

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_endpoint and self.llm_model and self.llm_api_key)

    def __post_init__(self):
        if self.mode not in {"local", "remote", "demo"}:
            raise ValueError("ORCHESTRATOR_MODE must be local, remote, or demo")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("AGENT_TIMEOUT_SECONDS must be a positive finite number")
        if self.routing_mode not in {"auto", "keyword", "llm"}:
            raise ValueError("ROUTING_MODE must be auto, keyword, or llm")
        if self.llm_api_style not in {"openai_v1", "inference"}:
            raise ValueError("AZURE_AI_API_STYLE must be openai_v1 or inference")
        if not math.isfinite(self.llm_timeout_seconds) or self.llm_timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be a positive finite number")
        if self.answer_mode not in {"auto", "evidence", "llm"}:
            raise ValueError("ANSWER_MODE must be auto, evidence, or llm")
        if not math.isfinite(self.synthesis_timeout_seconds) or self.synthesis_timeout_seconds <= 0:
            raise ValueError("SYNTHESIS_TIMEOUT_SECONDS must be a positive finite number")
        if self.llm_endpoint:
            parsed = urlsplit(self.llm_endpoint)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.fragment or parsed.query):
                raise ValueError("AZURE_AI_ENDPOINT must be an HTTPS URL without credentials, query, or fragment")
            if "/api/projects" in parsed.path:
                raise ValueError("Use the Foundry model endpoint, not the project endpoint")
        for name, url in self.agent_urls.items():
            parsed = urlsplit(url)
            if name not in NAMES or parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("Agent endpoints must be absolute HTTP(S) URLs")
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Use AGENT_TOKEN variables for credentials; URLs cannot have fragments")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            mode=os.getenv("ORCHESTRATOR_MODE", "local").lower(),
            routing_mode=os.getenv("ROUTING_MODE", "auto").lower(),
            answer_mode=os.getenv("ANSWER_MODE", "auto").lower(),
            synthesis_timeout_seconds=float(os.getenv("SYNTHESIS_TIMEOUT_SECONDS", "30")),
            llm_endpoint=os.getenv("AZURE_AI_ENDPOINT", "").strip().rstrip("/"),
            llm_model=(os.getenv("AZURE_AI_MODEL", "").strip()
                       or os.getenv("AZURE_AI_DEPLOYMENT", "").strip()),
            llm_api_key=os.getenv("AZURE_AI_API_KEY", ""),
            llm_api_style=os.getenv("AZURE_AI_API_STYLE", "openai_v1").lower(),
            llm_api_version=os.getenv("AZURE_AI_API_VERSION", "").strip(),
            llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "10")),
            timeout_seconds=float(os.getenv("AGENT_TIMEOUT_SECONDS", "20")),
            docs_root=Path(os.getenv("DOCS_ROOT", str(ROOT))).expanduser(),
            code_root=Path(os.getenv("CODE_ROOT", str(ROOT))).expanduser(),
            logs_root=Path(os.environ["LOGS_ROOT"]).expanduser() if os.getenv("LOGS_ROOT") else None,
            agent_urls={n: os.environ[f"{n.upper()}_AGENT_URL"] for n in NAMES
                        if os.getenv(f"{n.upper()}_AGENT_URL")},
            agent_tokens={n: os.environ[f"{n.upper()}_AGENT_TOKEN"] for n in NAMES
                          if os.getenv(f"{n.upper()}_AGENT_TOKEN")},
        )
