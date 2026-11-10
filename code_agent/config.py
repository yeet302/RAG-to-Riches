"""Configuration: hard boundary is a single approved public GitHub repository."""
import os
from dataclasses import dataclass

APPROVED_REPOSITORY = "open-telemetry/opentelemetry-demo"

@dataclass(frozen=True)
class Settings:
    project_endpoint: str
    model_deployment: str
    search_endpoint: str
    search_index: str
    github_token: str
    repo: str
    agent_name: str
    default_ref: str = ""  # Empty means the repository's current default branch.

    @classmethod
    def load(cls):
        from dotenv import load_dotenv
        load_dotenv()
        required = ['FOUNDRY_PROJECT_ENDPOINT', 'MODEL_DEPLOYMENT_NAME', 'AZURE_SEARCH_ENDPOINT']
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise RuntimeError('Missing environment variables: ' + ', '.join(missing))
        repo = os.getenv('CODE_REPO', APPROVED_REPOSITORY).strip()
        if repo != APPROVED_REPOSITORY:
            raise ValueError(f'Only {APPROVED_REPOSITORY} is approved for code investigation')
        return cls(os.environ['FOUNDRY_PROJECT_ENDPOINT'], os.environ['MODEL_DEPLOYMENT_NAME'],
                   os.environ['AZURE_SEARCH_ENDPOINT'], os.getenv('AZURE_SEARCH_INDEX','rag-to-riches-code'),
                   os.getenv('GITHUB_TOKEN',''), APPROVED_REPOSITORY,
                   os.getenv('FOUNDRY_CODE_AGENT_NAME','rag-to-riches-code-agent'),
                   os.getenv('CODE_REF','').strip())
