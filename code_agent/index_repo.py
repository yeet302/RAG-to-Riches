"""One-time/periodic GitHub -> Azure AI Search ingest. Run as python -m code_agent.index_repo."""
import base64
import hashlib
import requests
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchIndex, SearchField, SimpleField, SearchableField, SearchFieldDataType
from .config import Settings


def index_repo():
    s = Settings.load()
    credential = DefaultAzureCredential()
    client = SearchIndexClient(s.search_endpoint, credential)
    fields = [SimpleField(name='id', type=SearchFieldDataType.String, key=True),
              SearchableField(name='path', type=SearchFieldDataType.String, filterable=True),
              SearchableField(name='content', type=SearchFieldDataType.String),
              SimpleField(name='commit', type=SearchFieldDataType.String, filterable=True),
              SimpleField(name='start_line', type=SearchFieldDataType.Int32),
              SimpleField(name='end_line', type=SearchFieldDataType.Int32)]
    client.create_or_update_index(SearchIndex(name=s.search_index, fields=fields))
    index = SearchClient(s.search_endpoint, s.search_index, credential)
    session = requests.Session()
    session.headers['User-Agent'] = 'rag-to-riches-indexer'
    if s.github_token:
        session.headers['Authorization'] = f'Bearer {s.github_token}'
        
    def get(url, **kwargs):
        r = session.get(url, timeout=40, **kwargs)
        r.raise_for_status()
        return r.json()
    base = f'https://api.github.com/repos/{s.repo}'
    from .retrieval import CodeRetriever
    # Resolve once for the whole ingest to avoid mixing files across moving branches.
    sha = CodeRetriever(s).resolve_commit()
    tree = get(f'{base}/git/trees/{sha}', params={'recursive': 1})
    if tree.get('truncated'):
        raise RuntimeError('GitHub truncated tree; cannot guarantee complete indexing')
    suffixes = ('.java', '.py', '.go', '.ts', '.tsx', '.js', '.json', '.yaml', '.yml', '.md', '.proto', '.cs', '.rs')
    documents = []
    indexed_files = 0
    for item in tree['tree']:
        path = item['path']
        if item['type'] != 'blob' or not path.endswith(suffixes) or item.get('size', 0) > 150000:
            continue
        if any(part in path for part in ('node_modules/', 'dist/', 'vendor/', 'package-lock.json')):
            continue
        content = get(f'{base}/contents/{path}', params={'ref': sha})
        if content.get('encoding') != 'base64':
            continue
        raw = base64.b64decode(content['content']).decode('utf-8', 'replace').splitlines()
        indexed_files += 1
        for i in range(0, len(raw), 80):
            chunk = '\n'.join(f'{n+1}: {raw[n]}' for n in range(i, min(i+80, len(raw))))
            documents.append({'id': hashlib.sha256(f'{sha}:{path}:{i}'.encode()).hexdigest(),
                              'path': path, 'commit': sha, 'start_line': i+1,
                              'end_line': min(i+80, len(raw)), 'content': chunk})
            if len(documents) >= 500:
                results = index.upload_documents(documents)
                if any(not r.succeeded for r in results):
                    raise RuntimeError('One or more search documents failed indexing')
                documents.clear()
    if documents:
        results = index.upload_documents(documents)
        if any(not r.succeeded for r in results):
            raise RuntimeError('One or more search documents failed indexing')
    print(f'Indexed {indexed_files} files from {s.repo}@{sha} into {s.search_index}')

if __name__ == '__main__':
    index_repo()
