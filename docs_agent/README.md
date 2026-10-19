# Docs Agent MVP

**Project:** Microsoft Capstone – Multi-Agent Debugging System  


## Overview

The Docs Agent retrieves OpenTelemetry Demo documentation relevant to a debugging incident and returns structured findings with source links.

## Input

Currently, `main.py` uses a hardcoded incident to simulate Orchestrator input:

```json
{
  "incident_id": "incident_001",
  "service": "Ad Service",
  "operation": "GetAds",
  "user_query": "Why are some Ad Service requests failing?"
}
```

## Current Implementation

1. **Keyword extraction:** Read `service` and `operation`, then extract words of at least four letters from `user_query` using Python regex.
2. **Document fetching:** Use the **GitHub REST Contents API** to fetch predefined files (`README.md` and `src/ad/README.md`) from `open-telemetry/opentelemetry-demo`.
3. **Retrieval:** Match keywords against document lines in Python, rank lines by the number of matching terms, and return the top five with GitHub source URLs.
4. **Response:** Return a JSON-compatible dictionary. Debugging suggestions are currently **hardcoded**; no LLM is used.

## Output

Example structure (the actual run returned five excerpts):

```json
{
  "incident_id": "incident_001",
  "service": "Ad Service",
  "summary": "Found 5 matching documentation excerpts.",
  "relevant_docs": [
    {
      "source": "src/ad/README.md",
      "line": 1,
      "score": 2,
      "excerpt": "# Ad Service",
      "url": "https://github.com/open-telemetry/opentelemetry-demo/blob/main/src/ad/README.md#L1"
    }
  ],
  "suggested_checks": [
    "Inspect Ad Service logs and trace IDs.",
    "Compare failing requests with the documented configuration."
  ],
  "retrieval_errors": [],
  "analysis_method": "keyword_matching_no_llm"
}
```

## Current Limitations

- **Retrieval relevance:** Keyword matching often returns general service information instead of incident-specific troubleshooting guidance.
- **Limited coverage:** Only two predefined GitHub README files are searched.
- **Analysis:** Debugging suggestions are hardcoded, and Orchestrator integration is not yet implemented.


## Planned Improvements

- **Query Understanding:** Use an LLM to extract more useful debugging search terms.
- **Retrieval:** Expand documentation sources to include both the GitHub repository and the [official OpenTelemetry Demo documentation](https://opentelemetry.io/docs/demo/). Discover documents dynamically, use document chunking, and improve ranking (e.g., BM25 or semantic search).
- **Analysis:** Use an LLM to produce evidence-grounded findings and incident-specific debugging checks instead of fixed suggestions.
- **Integration:** Agree on the input/output JSON contract and connect to the Orchestrator.
- **Testing:** Add automated tests for retrieval, output structure, and API failures.

## Run

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
python -m pip install -r requirements.txt

# Run the Docs Agent
python main.py
```


Requires internet access to retrieve GitHub documentation.
