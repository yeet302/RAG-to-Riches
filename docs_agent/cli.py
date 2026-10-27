"""Local Azure analysis smoke test; does not change the HTTP contract."""
import argparse
import asyncio
import json

from .config import Settings
from .contracts import AgentRequest
from .main import DocsAgent


def main():
    parser = argparse.ArgumentParser(description='Run a real Azure-backed docs analysis.')
    parser.add_argument('--question', default='How do I build the Ad Service locally?')
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.configured:
        parser.exit(1, 'Azure is not configured. Set endpoint and model deployment in docs_agent/.env, plus an API key or AZURE_AI_AUTH_MODE=entra.\n')
    request = AgentRequest(request_id='local-azure-check', question=args.question,
                           task='Search documentation and cite exact sources.', intent='how_to')
    result = asyncio.run(DocsAgent(settings).run(request))
    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    return 0 if result.status in {'ok', 'no_evidence'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
