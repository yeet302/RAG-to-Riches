"""Environment configuration, compatible with the orchestrator's Azure settings."""
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    endpoint: str = ''
    model: str = ''
    api_key: str = field(default='', repr=False)
    api_style: str = 'openai_v1'
    api_version: str = ''
    token: str = field(default='', repr=False)
    timeout: float = 18
    fetch_timeout: float = 5
    llm_timeout: float = 10
    repo: str = 'open-telemetry/opentelemetry-demo'
    ref: str = 'main'
    paths: tuple = ('README.md', 'src/ad/README.md')

    github_roots: tuple = ('src/ad',)
    website_seeds: tuple = ('https://opentelemetry.io/docs/demo/',)
    max_web_pages: int = 16
    max_github_docs: int = 20
    discovery_timeout: float = 7

    def __post_init__(self):
        for value in (self.timeout, self.fetch_timeout, self.llm_timeout, self.discovery_timeout):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Timeouts must be positive and finite')
        if self.api_style not in {'openai_v1', 'inference'}:
            raise ValueError('AZURE_AI_API_STYLE must be openai_v1 or inference')
        if self.endpoint:
            parsed = urlsplit(self.endpoint)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment):
                raise ValueError('AZURE_AI_ENDPOINT must be a credential-free HTTPS URL')
        if len(self.paths) > 10:
            raise ValueError('DOCS_PATHS supports at most 10 optional seed files')
        if not 1 <= self.max_web_pages <= 100 or not 1 <= self.max_github_docs <= 100:
            raise ValueError('Document limits must be between 1 and 100')
        for seed in self.website_seeds:
            parsed = urlsplit(seed)
            if (parsed.scheme != 'https' or parsed.netloc != 'opentelemetry.io'
                    or not parsed.path.startswith('/docs/demo/') or parsed.query or parsed.fragment):
                raise ValueError('Website seeds must be official Demo documentation URLs')

    @property
    def configured(self):
        return bool(self.endpoint and self.model and self.api_key)

    @property
    def completion_url(self):
        endpoint = self.endpoint.rstrip('/')
        if endpoint.endswith('/chat/completions'):
            return endpoint
        if endpoint.endswith(('/openai/v1', '/models')):
            return endpoint + '/chat/completions'
        suffix = '/openai/v1/chat/completions' if self.api_style == 'openai_v1' else '/models/chat/completions'
        return endpoint + suffix

    @classmethod
    def from_env(cls):
        load_dotenv(Path(__file__).with_name('.env'), override=False)
        return cls(
            endpoint=(os.getenv('AZURE_AI_ENDPOINT') or os.getenv('AZURE_OPENAI_ENDPOINT', '')).strip(),
            model=(os.getenv('AZURE_AI_MODEL') or os.getenv('AZURE_OPENAI_DEPLOYMENT', '')).strip(),
            api_key=(os.getenv('AZURE_AI_API_KEY') or os.getenv('AZURE_OPENAI_API_KEY', '')).strip(),
            api_style=os.getenv('AZURE_AI_API_STYLE', 'openai_v1'),
            api_version=os.getenv('AZURE_AI_API_VERSION', ''),
            token=os.getenv('DOCS_AGENT_TOKEN', ''),
            timeout=float(os.getenv('DOCS_TIMEOUT_SECONDS', '18')),
            fetch_timeout=float(os.getenv('DOCS_FETCH_TIMEOUT_SECONDS', '5')),
            llm_timeout=float(os.getenv('LLM_TIMEOUT_SECONDS', '10')),
            repo=os.getenv('DOCS_REPO', 'open-telemetry/opentelemetry-demo'),
            ref=os.getenv('DOCS_REF', 'main'),
            github_roots=tuple(p.strip() for p in os.getenv('DOCS_GITHUB_ROOTS', 'src/ad').split(',') if p.strip()),
            website_seeds=tuple(p.strip() for p in os.getenv('DOCS_WEBSITE_SEEDS', 'https://opentelemetry.io/docs/demo/').split(',') if p.strip()),
            max_web_pages=int(os.getenv('DOCS_MAX_WEB_PAGES', '16')),
            max_github_docs=int(os.getenv('DOCS_MAX_GITHUB_DOCS', '20')),
            discovery_timeout=float(os.getenv('DOCS_DISCOVERY_TIMEOUT_SECONDS', '7')),
            paths=tuple(p.strip() for p in os.getenv('DOCS_PATHS', 'README.md,src/ad/README.md').split(',') if p.strip()),
        )
