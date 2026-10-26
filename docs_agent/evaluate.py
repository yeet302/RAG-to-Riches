"""Run three acceptance questions; offline by default, --live opts into Azure."""
import argparse
import asyncio
import json
import time
from pathlib import Path

from .contracts import AgentRequest
from .main import DocsAgent
from .tests.support import CASES, offline_agent


async def evaluate(live):
    agent = DocsAgent() if live else offline_agent()
    if live and not agent.settings.configured:
        raise SystemExit('Azure is not configured. Set AZURE_AI_ENDPOINT, AZURE_AI_MODEL and AZURE_AI_API_KEY.')
    rows = []
    for case in CASES:
        request = AgentRequest(request_id='eval-' + case['id'], question=case['question'],
                               task='Search documentation and cite exact sources.', intent='how_to')
        start = time.perf_counter()
        result = await agent.run(request)
        expected_terms = all(term.lower() in result.summary.lower() for term in case['expected'])
        rows.append({'id': case['id'], 'question': case['question'],
                     'expected_status': case['status'], 'expected_terms': case['expected'],
                     'elapsed_ms': round((time.perf_counter() - start) * 1000, 2),
                     'automatic_check_passed': result.status == case['status'] and expected_terms,
                     'finding': result.model_dump()})
    return {'mode': 'live_azure' if live else 'offline_mock_model',
            'note': 'Automatic checks are not a semantic quality assessment. Review factual support manually. '
                    'Offline timing excludes network and model inference.', 'cases': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.live))
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text + '\n')
    print(text)
    raise SystemExit(0 if all(row['automatic_check_passed'] for row in report['cases']) else 1)
