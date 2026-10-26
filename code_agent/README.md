# RAG-to-Riches: Azure-native Code Agent

**Azure Foundry-hosted LLM + Foundry Agent Service + Azure AI Search + GitHub REST API**.
The agent dynamically calls `search_code`, `read_source`, and optionally `get_issue`, then returns the existing orchestrator `AgentFinding` interface.

## Quick start

1. Provision a **Microsoft Foundry** project and deploy a tool-calling model. Provision **Azure AI Search** (Basic or higher as required by your setup). Grant your signed-in developer identity **Foundry User** on the project and **Search Index Data Contributor** plus **Search Service Contributor** (or appropriately scoped index-management permissions) on the search resource. Azure role assignments may take time to propagate.
2. Install Python 3.11+, Azure CLI. Run `az login`.
3. From repository root: `python -m pip install -r requirements.txt` (this is the artifact's requirements; after copy, use `code_agent/requirements.txt` if you move it there).
4. Copy `.env.example` to `.env`; fill in the actual Azure project, deployed model and Search endpoint. **Do not commit `.env`.**
5. **Index code:** `python -m code_agent.index_repo` (the first ingest can require significant time and GitHub API quota; use `GITHUB_TOKEN` if needed).
6. **Ask a real question:** `python -m code_agent.cli "Why does GetAds sometimes fail with UNAVAILABLE?"`
7. Test more questions, such as `"Where is the Ad Service high CPU load generated?"` and `"How does forced garbage collection affect the Ad Service?"`.

### Integration with your team's orchestrator

Copy `code_agent/` into the **root** of the locally integrated RAG-to-Riches checkout. Add its requirements to your backend environment. Modify **only the orchestrator branch in your local integration branch**:

```python
# orchestrator/orchestrator.py (the app starts in orchestrator/ working directory)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from code_agent import CodeAgent

AGENTS = {
    'logs': DemoAgent('logs'),
    'docs': DemoAgent('docs'),
    'code': CodeAgent(),
}
```

`context['evidence_bundle']` is supplied by the existing orchestrator. For complete end-to-end live responses, the teammates must also replace hard-coded orchestrator synthesis and demo Logs/Docs agents. Its UI currently shows a fixed 'Demo responses' badge.

**Important:** the branch `logs_agent` currently works over **OpenStack** sample logs, not the OpenTelemetry Ad Service. Fix the shared dataset before claiming real log-to-code conclusions. Current orchestrator's chosen Git revision is `e5552ee25bcede2900d89188f076c464cd73c074`; keep all 3 agents aligned on it. The agent distinguishes a potentially executable failure code path from evidence that the flag actually is enabled.

## Design and guardrails

- Search AI Search index (text search initially; can add embeddings/hybrid retrieval later).
- For evidence, **retrieve full source via GitHub at the resolved immutable SHA**; output GitHub permalinks. Agent-reported line ranges are accepted only if the agent actually retrieved those lines.
- Agent output: `AgentFinding(agent='code', status, summary, hypotheses, evidence, sources_searched, confidence, latency_ms)`. No fabricated log evidence or made-up GitHub line references.
- Not storing ground-truth causes in the model prompt or code.
- First call creates a versioned Foundry agent; subsequent calls in same process reuse it. A persistent Foundry agent is used; it is not an Ollama loop.
- This starter does not yet add vector embeddings, automated index synchronization, ingestion batching retries, cost controls, long-running rate-limit handling, service telemetry or integration tests requiring your credentials.
- For production, add index-level filtering by commit; current `search_code` filters results after Azure query, which may reduce result count if several commits share an index. Prefer a dedicated index per chosen Git revision as configured here.

## Three sprint acceptance questions

1. Why are 10% of Ad Service `GetAds` calls returning `UNAVAILABLE`?
2. What part of Ad Service could explain high CPU when `adHighCpu` is enabled?
3. How can `adManualGc` produce pauses or latency?

Evaluate each on *real Foundry runs*: capture returned source citations, check the actual lines and explanations manually. Static compilation is not sufficient to claim these tests passed.

## Security

Do not put secrets, tokens or confidential repositories in prompts, logs, test fixtures or GitHub commits. Set least-privilege Azure roles and use managed identity in hosted environments. The example GitHub source is public. A Search index may contain complete code snippets; restrict access appropriately.

## Repository scope and Git history

The agent is **hard-restricted to `open-telemetry/opentelemetry-demo`**, but it is **not restricted to one commit**. Empty `CODE_REF` follows the current default branch; set `CODE_REF` to a branch, tag or SHA to index and query that revision. The LLM may choose `ref` per tool call. All source citations use the *resolved immutable SHA* so citations remain accurate after new GitHub commits.

**Index freshness:** after a branch changes, rerun `python -m code_agent.index_repo` to update that branch's code in Azure AI Search. Searching a revision that has not been indexed returns no indexed hits; the agent can still discover files and read GitHub directly. For a single incident, all code findings should ideally use the same revision as the incident's logs and documentation.
