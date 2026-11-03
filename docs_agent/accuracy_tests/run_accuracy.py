"""Run fixed bug questions through the existing agent; keep truth outside prompts."""
import argparse
import asyncio
import csv
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from docs_agent.config import Settings
from docs_agent.contracts import AgentRequest
from docs_agent.main import DocsAgent

ROOT = Path(__file__).resolve().parent
PINNED_REF = '5c8d404a85380226ff45a39dcb4159eda9552c56'
FLAGS_URL = 'https://opentelemetry.io/docs/demo/feature-flags/'


class ObservedDocsAgent(DocsAgent):
    """Capture the same retrieval used by run(), without doing a second search."""
    async def retrieve(self, question):
        result = await super().retrieve(question)
        self.retrieved = result[0]
        return result


def fixture_response(request):
    if (request.url.host == 'api.github.com'
            and request.url.path == '/repos/open-telemetry/opentelemetry-demo/contents/src/ad/README.md'
            and request.url.params.get('ref') == PINNED_REF):
        return httpx.Response(200, text=(ROOT.parent / 'tests/fixtures/ad_README.md').read_text())
    if str(request.url) == FLAGS_URL:
        return httpx.Response(200, text=(ROOT / 'fixtures/feature_flags.html').read_text())
    return httpx.Response(404)


def make_agent(live=False, corpus='fixture'):
    settings = Settings.from_env()
    if live and not settings.configured:
        raise ValueError('Azure is not configured; set AZURE_AI_ENDPOINT, AZURE_AI_MODEL and AZURE_AI_API_KEY (or supported aliases).')
    if not live:
        settings = replace(settings, endpoint='', model='', api_key='')
    transport = None
    if corpus == 'fixture':
        settings = replace(settings, repo='open-telemetry/opentelemetry-demo', ref=PINNED_REF,
                           paths=('src/ad/README.md',), github_roots=(), website_seeds=(FLAGS_URL,))
        transport = httpx.MockTransport(fixture_response)
    return ObservedDocsAgent(settings, docs_transport=transport)


def document_hit(targets, evidence):
    """Require source identity AND supporting text, not just a page-name match."""
    if not targets:
        return None
    return all(any(target['source'] in (item.get('ref', '') + ' ' + (item.get('link') or ''))
                   and all(term.casefold() in (item.get('snippet') or '').casefold()
                           for term in target['terms'])
                   for item in evidence) for target in targets)


async def run_case(agent, case, run_id, live):
    agent.retrieved = []
    request = AgentRequest(request_id=run_id + '-' + case['id'], question=case['question'],
                           task='Search documentation and cite exact sources.', intent='incident')
    start = time.perf_counter()
    try:
        result = (await agent.run(request)).model_dump(mode='json')
    except Exception as exc:
        # Keep failed trials and continue the suite. Never serialize request headers/settings.
        result = {'status': 'error', 'summary': 'Evaluation call raised ' + type(exc).__name__,
                  'evidence': [], 'hypotheses': []}
    elapsed = round(time.perf_counter() - start, 3)
    retrieved = [item.model_dump(mode='json') for item in agent.retrieved]
    failed = result['status'] in {'error', 'no_access'}
    return {
        'bug': case['id'], 'question': case['question'], 'expected_cause': case['expected_cause'],
        'expected_docs': case['targets'], 'review_rule': case['review_rule'],
        'agent_answer': result['summary'], 'correct?': 'No' if failed else 'REVIEW',
        'fix_worked?': 'Not tested' if case['targets'] else 'N/A',
        'time_taken_seconds': elapsed, 'agent_status': result['status'],
        'retrieval_hit_at_5': document_hit(case['targets'], retrieved),
        'final_evidence_hit': document_hit(case['targets'], result.get('evidence', [])) if live else None,
        'reviewer_notes': 'Agent/source failure' if failed else '',
        'retrieved_evidence': retrieved, 'finding': result,
    }


def summarize(rows, live):
    positives = [row for row in rows if row['expected_docs']]
    reviewed = [row for row in rows if row['correct?'] in {'Yes', 'No'}]
    correct = sum(row['correct?'] == 'Yes' for row in reviewed)
    complete = len(reviewed) == len(rows)
    return {
        'total_cases': len(rows), 'reviewed_cases': len(reviewed),
        'retrieval_hits': sum(row['retrieval_hit_at_5'] is True for row in positives),
        'retrieval_cases': len(positives),
        'retrieval_hit_rate_at_5': (sum(row['retrieval_hit_at_5'] is True for row in positives) / len(positives)) if positives else None,
        'confirmed_accuracy': correct / len(rows) if live and complete and rows else None,
        'average_seconds': sum(row['time_taken_seconds'] for row in rows) / len(rows) if rows else None,
        'errors': sum(row['agent_status'] in {'error', 'no_access'} for row in rows),
    }


def write_csv(path, rows, run_id, mode=None, corpus=None):
    fields = ['run_id', 'mode', 'corpus', 'bug', 'question', 'expected_cause', 'agent_answer', 'correct?',
              'fix_worked?', 'time_taken_seconds', 'expected_docs', 'returned_docs',
              'retrieval_hit_at_5', 'final_evidence_hit', 'agent_status', 'reviewer_notes']
    with path.open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            record = {key: row.get(key) for key in fields}
            record.update(run_id=run_id, mode=mode, corpus=corpus, expected_docs=json.dumps(row['expected_docs']),
                          returned_docs=' | '.join(item.get('link') or item['ref']
                                                   for item in row['finding'].get('evidence', [])))
            # Keep imported model/document text from becoming spreadsheet formulas.
            writer.writerow({key: "'" + value if isinstance(value, str)
                             and value.lstrip().startswith(('=', '+', '-', '@')) else value
                             for key, value in record.items()})


async def evaluate(args):
    cases = json.loads((ROOT / 'cases.json').read_text())
    if args.case != 'all':
        cases = [case for case in cases if case['id'] == args.case]
    if not cases:
        raise ValueError('Unknown case ID')
    agent = make_agent(args.live, args.corpus)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    output = args.output_dir / run_id
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    report = {
        'run_id': run_id, 'mode': 'live_azure' if args.live else 'retrieval_only',
        'corpus': args.corpus, 'github_ref': agent.settings.ref,
        'model': agent.settings.model if args.live else None,
        'document_settings': {
            'repo': agent.settings.repo, 'ref': agent.settings.ref,
            'paths': list(agent.settings.paths), 'github_roots': list(agent.settings.github_roots),
            'website_seeds': list(agent.settings.website_seeds),
            'max_web_pages': agent.settings.max_web_pages,
            'max_github_docs': agent.settings.max_github_docs,
        },
        'note': 'Controlled bug descriptions; no runtime faults or fixes were executed. '
                'Document hits are automatic screening, not semantic accuracy. '
                'Live corpus pages may change; saved snippets document what was observed. '
                'CSV requires manual review and transfer to the shared sheet.',
        'cases': rows,
    }
    for case in cases:
        row = await run_case(agent, case, run_id, args.live)
        row.update(mode=report['mode'], corpus=report['corpus'])
        rows.append(row)
        (output / (case['id'] + '.json')).write_text(json.dumps(row, indent=2) + '\n')
        report['summary'] = summarize(rows, args.live)
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        write_csv(output / 'accuracy_results.csv', rows, run_id, report['mode'], report['corpus'])
        print(case['id'], row['agent_status'], 'retrieval hit:', row['retrieval_hit_at_5'], flush=True)
    print(json.dumps(report['summary'], indent=2))
    print('Saved:', output)
    return 1 if report['summary']['errors'] else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__ + ' Default: online documents and real Azure model.')
    parser.add_argument('--live', action='store_true', default=True, help='Use real Azure generation (default; may incur charges).')
    parser.add_argument('--retrieval-only', action='store_false', dest='live', help='Diagnostic mode: skip Azure generation.')
    parser.add_argument('--corpus', choices=['fixture', 'live'], default='live')
    parser.add_argument('--case', default='all', choices=['all', 'D01', 'D02', 'D03', 'D04', 'D05', 'D06'])
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs')
    return parser.parse_args(argv)


def main():
    args = parse_args()
    try:
        return asyncio.run(evaluate(args))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
