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

    def __post_init__(self):
        if self.mode not in {"local", "remote", "demo"}:
            raise ValueError("ORCHESTRATOR_MODE must be local, remote, or demo")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("AGENT_TIMEOUT_SECONDS must be a positive finite number")
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
            timeout_seconds=float(os.getenv("AGENT_TIMEOUT_SECONDS", "20")),
            docs_root=Path(os.getenv("DOCS_ROOT", str(ROOT))).expanduser(),
            code_root=Path(os.getenv("CODE_ROOT", str(ROOT))).expanduser(),
            logs_root=Path(os.environ["LOGS_ROOT"]).expanduser() if os.getenv("LOGS_ROOT") else None,
            agent_urls={n: os.environ[f"{n.upper()}_AGENT_URL"] for n in NAMES
                        if os.getenv(f"{n.upper()}_AGENT_URL")},
            agent_tokens={n: os.environ[f"{n.upper()}_AGENT_TOKEN"] for n in NAMES
                          if os.getenv(f"{n.upper()}_AGENT_TOKEN")},
        )
