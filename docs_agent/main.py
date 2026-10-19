import json
import re
import requests

# choose the repo and a list of documentation files
# TODO: Automatically discover relevant docs instead of using fixed paths.
REPO = 'open-telemetry/opentelemetry-demo'
DOC_PATHS = ['README.md', 'src/ad/README.md',"src/adservice/README.md"]

#Fetch documentation content using the GitHub REST API.
def fetch_document(path):

    # Build the GitHub Contents API URL.
    url = f'https://api.github.com/repos/{REPO}/contents/{path}'

    # Request the raw content of the documentation file.
    response = requests.get(url, headers={'Accept': 'application/vnd.github.raw+json'}, timeout=15)

     # Raise an exception if the request fails.
    response.raise_for_status()
    return response.text


# Find relevant documentation using keyword matching.
def search_docs(incident):

    # Extract keywords from the incident information.
    #["Ad Service", "GetAds"]
    # Currently uses regex; TODO: use an LLM for better query understanding.
    terms = [incident['service'], incident['operation']]

     # Convert keywords to lowercase and remove duplicates.
     #["some", "Service", "requests", "failing"]
     # validate the retrival workflow first
    terms += re.findall(r'[A-Za-z]{4,}', incident['user_query'])
    terms = list(dict.fromkeys(t.lower() for t in terms))
    
    matches = []
    errors = []
  

    # Fetch each predefined documentation file.
    for path in DOC_PATHS:
        try:
            content = fetch_document(path)
        except requests.RequestException as exc:
            # Record API errors and continue with other files.
            errors.append(f'{path}: {exc}')
            continue

        # Search the documentation line by line.
        # keyword matching and scoring
        # TODO: Use document chunking to preserve more context.
        for line_no, line in enumerate(content.splitlines(), start=1):

            # Skip empty lines and overly long lines.
            if not line.strip() or len(line) > 500:
                continue

            # Calculate relevance based on the number of matched keywords.
            # how many extracted keywords appear in each line, only checks the keyword overlap
            # TODO: Improve ranking with BM25 or semantic search.    
            score = sum(term in line.lower() for term in terms)
            if score:
                # Store matching text and its GitHub source reference.
                matches.append({'source': path, 'line': line_no, 'score': score, 'excerpt': line.strip(), 'url': f'https://github.com/{REPO}/blob/main/{path}#L{line_no}'})
    


    # Rank matches by relevance and return the top five.
    matches.sort(key=lambda m: m['score'], reverse=True)
    return matches[:5], errors

#Run the Docs Agent and return structured findings.
def run_agent(incident):

    # Retrieve relevant documentation.
    matches, errors = search_docs(incident)

    # Build the JSON-compatible response for the Orchestrator.
    return {
        'incident_id': incident['incident_id'],
        'service': incident['service'],
        'summary': f'Found {len(matches)} matching documentation excerpts.' if matches else 'No relevant documentation excerpts found.',
        'relevant_docs': matches,
        'suggested_checks': ['Inspect Ad Service logs and trace IDs.', 'Compare failing requests with the documented configuration.'] if matches else [],
        'retrieval_errors': errors,
        'analysis_method': 'keyword_matching_no_llm',
    }

# Simulate incident input from the Orchestrator.
# TODO: Replace with actual Orchestrator integration.
if __name__ == '__main__':
    incident = {'incident_id': 'incident_001', 'service': 'Ad Service', 'operation': 'GetAds', 'user_query': 'Why are some Ad Service requests failing?'}

    #Run the agent and print the formatted JSON output.
    print(json.dumps(run_agent(incident), indent=2))
