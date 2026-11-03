"""Run unchanged CodeAgent with real Foundry and controlled code retrieval. No ground truth in prompt."""
import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(REPO_ROOT))
from retriever import FixtureRetriever

async def run_one(case, output):
    from code_agent.agent import CodeAgent
    from code_agent.config import Settings
    fixture_root = ROOT / 'fixtures' / case['fixture']
    settings = Settings.load()
    retriever = FixtureRetriever(fixture_root)
    agent = CodeAgent(settings=settings, retriever=retriever)
    start = time.perf_counter()
    finding = await agent.run(case['question'], {'incident_id':case['id'], 'task':'Investigate the reported defect; identify source lines and recommend a testable correction.'})
    seconds = round(time.perf_counter()-start, 3)
    result = finding.model_dump(mode='json')
    (output/(case['id']+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    refs = [e.get('ref','') for e in result.get('evidence',[])]
    return {'bug':case['id'], 'expected_cause':case['expected_cause'], 'agent_answer':result.get('summary',''),
            'correct?':'REVIEW', 'fix_worked?':'NOT TESTED', 'time_taken_seconds':seconds,
            'code_references':' | '.join(refs), 'expected_file':case['expected_file'],
            'expected_line':case['expected_line'], 'agent_status':result.get('status',''),
            'reviewer_notes':'Manually assess cause, source-line overlap and proposed fix against expected cause. Apply proposed fix and rerun regression.'}

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case',default='all')
    args = parser.parse_args()
    cases=json.loads((ROOT/'cases.json').read_text(encoding='utf-8'))
    selected = [c for c in cases if args.case=='all' or c['id']==args.case]
    if not selected:
        parser.error('Unknown case')
    output=ROOT/'outputs'; output.mkdir(exist_ok=True)
    rows=[]
    for case in selected:
        print('Running actual Foundry investigation:',case['id'],flush=True)
        row=await run_one(case,output)
        print(case['id'],row['agent_status'],row['time_taken_seconds'],'seconds',flush=True)
        rows.append(row)
    path=output/'accuracy_results.csv'
    with path.open('w',newline='',encoding='utf-8') as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print('Review results:',path)

if __name__=='__main__':
    asyncio.run(main())
