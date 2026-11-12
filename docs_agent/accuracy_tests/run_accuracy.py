"""Run fixed bug questions through the existing agent; keep truth outside prompts."""

# Comparison flow
# 1. Read each question. No Docs sends it directly to Azure without document retrieval.
# 2. Extract keywords locally to prioritize which GitHub files and official pages to fetch.
# 3. Rank fetched excerpts by the number of distinct question words they contain.
# 4. Reuse that ranking for both variants. Top 5 takes five excerpts and All Docs takes every match.
# 5. With Azure enabled and matching evidence available, the Docs Agent sends the question and
#    selected excerpts to Azure for analysis, then validates the answer's citations.
# 6. Save each variant's excerpts and unscored response in raw_results.json.
import argparse
import asyncio
import csv
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from docs_agent.config import Settings
from docs_agent.contracts import AgentRequest, Evidence
from docs_agent.main import DocsAgent

ROOT = Path(__file__).resolve().parent
HISTORICAL_CASES = ROOT / '261103-Accuracy Test - Cases.json'
COMPARISON_CASES = ROOT / '261112-Root Cause Comparison - Cases.json'
DOC_VERSIONS = ('top5', 'all-doc')
VERSIONS = ('nodoc', *DOC_VERSIONS)
PINNED_REF = '5c8d404a85380226ff45a39dcb4159eda9552c56'


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
    return httpx.Response(404)


# Configure the Docs Agent for Azure generation or retrieval-only execution.
def make_agent(live=False, corpus='fixture'):
    """Configure Azure generation and select live sources or the local fixture."""
    settings = Settings.from_env()
    if live and not settings.configured:
        raise ValueError('Azure is not configured; set AZURE_AI_ENDPOINT, AZURE_AI_MODEL and AZURE_AI_API_KEY (or supported aliases).')
    if not live:
        settings = replace(settings, endpoint='', model='', api_key='')
    transport = None
    if corpus == 'fixture':
        settings = replace(settings, repo='open-telemetry/opentelemetry-demo', ref=PINNED_REF,
                           paths=('src/ad/README.md',), github_roots=(), website_seeds=())
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


# Run one historical case and capture its answer, evidence, and timing.
async def run_case(agent, case, run_id, live):
    """Run one historical case and collect its answer and retrieval metrics."""
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


# Aggregate retrieval and review metrics for the historical run.
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


# Export the historical results for review in the shared sheet.
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


# Run the historical suite and write its JSON and CSV outputs.
async def evaluate(args):
    """Run the historical suite and write its per-case JSON, report, and CSV."""
    cases = load_cases(args.cases, args.case, HISTORICAL_CASES)
    if any(not {'expected_cause', 'targets', 'review_rule'} <= case.keys()
           or not case['id'].isalnum() for case in cases):
        raise ValueError('Legacy cases require alphanumeric IDs and the original review fields.')
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




# Validate the input JSON and select either one case or the full suite.
def load_cases(path, case_id, default):
    """Resolve and validate a case file, then select one case or the full suite."""
    path = Path(path) if path else default
    if not path.is_absolute() and not path.is_file():
        path = ROOT / path
    cases = json.loads(path.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError('The case file must contain a nonempty JSON array.')
    ids = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str)
                or not case['id'].strip() or not isinstance(case.get('question'), str)
                or not 1 <= len(case['question'].strip()) <= 2000):
            raise ValueError('Each case needs an id and a question of 1 to 2000 characters.')
        if case['id'] in ids:
            raise ValueError(f"Duplicate case ID: {case['id']}")
        ids.add(case['id'])
    selected = [case for case in cases if case_id == 'all' or case['id'] == case_id]
    if not selected:
        raise ValueError(f'Unknown case ID: {case_id}')
    return selected


# Reuse the ranked excerpts in the normal Docs Agent answer pipeline.
class FixedEvidenceDocsAgent(DocsAgent):
    """Run the regular Docs Agent answer path against a snapshot."""
    def __init__(self, agent, retrieval, *, on_usage=None):
        super().__init__(agent.settings, llm_transport=agent.llm_transport, on_usage=on_usage)
        self.retrieval = retrieval

    async def retrieve(self, question):
        """Return the prepared excerpts and source status for this variant."""
        return self.retrieval


# Ask the same Azure model to diagnose the issue using only the original question.
async def run_no_docs(agent, question):
    messages = [
        {'role': 'system', 'content': (
            'Answer the debugging question using the information in the question and your general knowledge. '
            'Identify the most likely cause and explain how it produces the symptom. '
            'If the cause is uncertain, say what to check. Do not invent sources or observations.'
        )},
        {'role': 'user', 'content': question},
    ]
    answer = await asyncio.wait_for(
        agent.request_completion(messages), timeout=agent.settings.timeout)
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('No Docs returned an empty or invalid answer.')
    return {'status': 'ok', 'summary': answer, 'evidence': []}


# Load the previous All Docs inputs so a replay keeps the original ranking.
def load_snapshot(path, cases, model):
    report = json.loads(path.read_text())
    if report['settings']['docs_model'] != model:
        raise ValueError('The snapshot used a different Azure model.')
    previous = {row['case_id']: row for row in report['results'] if row['version'] == 'all-doc'}
    snapshots = {}
    for case in cases:
        row = previous.get(case['id'])
        if (not row or row['question'] != case['question'].strip()
                or 'docs_sources_searched' not in row):
            raise ValueError(f"No saved All Docs input for case {case['id']}.")
        snapshots[case['id']] = (
            [Evidence.model_validate(item) for item in row['docs_input']],
            row['docs_sources_searched'], row['docs_sources_unavailable'],
        )
    return snapshots


# Compare the question-only baseline with the Docs Agent variants.
async def compare(args):
    """Run each selected variant against shared retrieval and save raw responses."""
    # Read the questions and send them directly to the Docs Agent.
    cases = load_cases(args.cases, args.case, COMPARISON_CASES)
    versions = args.versions or (VERSIONS if args.live else DOC_VERSIONS)
    if 'nodoc' in versions and not args.live:
        raise ValueError('No Docs requires Azure generation. Remove --retrieval-only or omit nodoc.')
    use_docs = any(version in DOC_VERSIONS for version in versions)
    if len(set(versions)) != len(versions):
        raise ValueError('Each version can be selected only once.')
    docs_agent = make_agent(args.live, args.corpus)
    snapshots = (load_snapshot(args.replay, cases, docs_agent.settings.model)
                 if args.replay and use_docs else {})
    started = datetime.now(ZoneInfo('America/Chicago'))
    run_id = started.strftime('%y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
    output = args.output_dir / run_id
    output.mkdir(parents=True, exist_ok=False)
    raw_path = output / 'raw_results.json'
    report = {
        'run_id': run_id,
        'started_at': started.isoformat(),
        'versions': list(versions),
        'replay_source': str(args.replay) if args.replay else None,
        'cases': [{key: case[key] for key in ('id', 'question')} for case in cases],
        'settings': {
            'docs_model': docs_agent.settings.model or None,
            'docs_repo': docs_agent.settings.repo,
            'docs_ref': docs_agent.settings.ref,
            'corpus': args.corpus,
        },
        'results': [],
    }
    for case in cases:
        question = case['question'].strip()
        retrieval_error = None
        # Retrieve and rank once per question so both variants use the same candidates.
        retrieval_elapsed = None
        if snapshots:
            ranked, searched, unavailable = snapshots[case['id']]
        elif use_docs:
            retrieval_start = time.perf_counter()
            try:
                ranked, searched, unavailable = await asyncio.wait_for(
                    docs_agent.retrieve_ranked(question), timeout=docs_agent.settings.timeout)
            except Exception as exc:
                retrieval_error = {'stage': 'docs_retrieval', 'type': type(exc).__name__}
            retrieval_elapsed = round(time.perf_counter() - retrieval_start, 3)
        for version in versions:
            row = {'case_id': case['id'], 'question': question, 'version': version,
                   'docs_input': [], 'response': None, 'error': None, 'azure_usage': None,
                   'shared_retrieval_seconds': None if version == 'nodoc' else retrieval_elapsed}
            usage = []
            start = time.perf_counter()
            try:
                if version == 'nodoc':
                    baseline = DocsAgent(docs_agent.settings, llm_transport=docs_agent.llm_transport,
                                         on_usage=usage.append)
                    row['response'] = await run_no_docs(baseline, question)
                    continue
                if retrieval_error:
                    row['error'] = retrieval_error
                    continue
                # Top 5 takes the first five ranked excerpts. All Docs takes every match.
                evidence = ranked[:5] if version == 'top5' else ranked
                row['docs_input'] = [item.model_dump(mode='json') for item in evidence]
                row['docs_sources_searched'] = searched
                row['docs_sources_unavailable'] = unavailable
                request = AgentRequest(
                    request_id=f'{run_id}-{case["id"]}-{version}', question=question,
                    task='Search documentation and cite exact sources.', intent='incident',
                )
                # Run the normal Docs Agent analysis with these excerpts and skip a second fetch.
                response = await FixedEvidenceDocsAgent(
                    docs_agent, (evidence, searched, unavailable), on_usage=usage.append).run(request)
                row['response'] = response.model_dump(mode='json')
            except Exception as exc:
                row['error'] = {'stage': 'azure_baseline' if version == 'nodoc' else 'docs_agent',
                                'type': type(exc).__name__}
            finally:
                row['azure_usage'] = usage[0] if usage else None
                row['elapsed_seconds'] = round(time.perf_counter() - start, 3)
                report['results'].append(row)
                # Save the unscored response and its input after each attempt.
                temp_path = raw_path.with_suffix('.tmp')
                temp_path.write_text(json.dumps(report, indent=2) + '\n')
                temp_path.replace(raw_path)
                print(case['id'], version, 'error' if row['error'] else row['response']['status'], flush=True)
    print('Saved:', raw_path)
    return int(any(row['error'] or row['response']['status'] in {'error', 'no_access'} for row in report['results']))


# Define the CLI choices and defaults for this runner.
def parse_args(argv=None):
    """Read CLI options for case selection, Docs Agent execution, and output."""
    parser = argparse.ArgumentParser(description=__doc__ + ' Default: online documents and real Azure model.')
    # Use --live (default) for Azure answers or --retrieval-only to skip Azure generation.
    parser.add_argument('--live', action='store_true', default=True, help='Use real Azure generation (default; may incur charges).')
    parser.add_argument('--retrieval-only', action='store_false', dest='live', help='Diagnostic mode: skip Azure generation.')
    # Use --corpus live (default) for online sources or --corpus fixture for local documents.
    parser.add_argument('--corpus', choices=['fixture', 'live'], default='live',
                        help='Use online sources (default) or a local document fixture.')
    # Use --cases FILE to pick a JSON file. Omit it to use the suite for the selected mode.
    parser.add_argument('--cases', type=Path, help='Case JSON filename or path. Defaults to the suite for the selected mode.')
    # Choose any of nodoc, top5, and all-doc. Live runs default to all three.
    parser.add_argument('--versions', nargs='+', choices=VERSIONS, help='Run any of nodoc, top5, and all-doc. Defaults to all available versions.')
    # Use --replay to reuse ranked inputs from a previous raw results file.
    parser.add_argument('--replay', type=Path, help='Replay saved All Docs inputs instead of fetching documents.')
    # Use --legacy for the November 3 workflow. The default is the comparison.
    parser.add_argument('--legacy', action='store_true', help='Replay the November 3 Docs Agent-only evaluation.')
    # Use --case B02 to run only B02. The default is all cases in the selected file.
    parser.add_argument('--case', default='all', help='Run one case ID, or all cases by default.')
    # Use --output-dir PATH to override the default accuracy_tests/outputs directory.
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs',
                        help='Parent directory for run outputs (default: accuracy_tests/outputs).')
    return parser.parse_args(argv)


# Select the comparison or historical workflow from the parsed options.
def main():
    """Dispatch the CLI to the comparison or historical evaluation."""
    args = parse_args()
    if args.legacy and (args.versions or args.replay):
        raise SystemExit('--versions and --replay are available for Docs Agent comparisons only.')
    try:
        return asyncio.run(evaluate(args) if args.legacy else compare(args))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == '__main__':
    raise SystemExit(main())
