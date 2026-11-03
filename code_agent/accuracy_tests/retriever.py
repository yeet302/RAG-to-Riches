"""Offline source adapter for real Foundry agent runs. No edits to code_agent/."""
import re
from pathlib import Path

FIXTURE_SHA = 'a' * 40  # stable fixture revision, intentionally not a GitHub commit

class FixtureRetriever:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.files = {p.relative_to(self.root).as_posix(): p for p in self.root.rglob('*') if p.is_file() and p.suffix == '.py' and 'tests' not in p.parts}

    def resolve_commit(self, ref=None):
        if ref and ref != FIXTURE_SHA:
            raise ValueError('Only the fixed fixture revision is supported')
        return FIXTURE_SHA

    def _path(self, path):
        if path not in self.files:
            raise ValueError('Source file not in fixture')
        return self.files[path]

    def list_repository_files(self, path_prefix='', limit=150, ref=None):
        sha = self.resolve_commit(ref)
        paths = sorted(p for p in self.files if p.startswith(path_prefix))
        limit = max(1, min(500, int(limit)))
        return {'files': paths[:limit], 'total_matching': len(paths), 'truncated': len(paths) > limit, 'commit': sha}

    def search_code(self, query, top=6, ref=None):
        sha = self.resolve_commit(ref)
        words = set(re.findall(r'[a-zA-Z][a-zA-Z0-9_]+', query.lower()))
        ranked = []
        for path, file in self.files.items():
            lines = file.read_text(encoding='utf-8').splitlines()
            for i in range(0, len(lines), 60):
                text = '\n'.join(f'{j+1}: {lines[j]}' for j in range(i, min(i+60, len(lines))))
                haystack = (path + ' ' + text).lower()
                score = sum(1 for word in words if word in haystack)
                if score:
                    ranked.append((score, path, i, text, len(lines)))
        ranked.sort(key=lambda x: (-x[0], x[1], x[2]))
        return [{'path':p, 'start_line':i+1, 'end_line':min(i+60,n), 'content':content[:5000], 'commit':sha} for _,p,i,content,n in ranked[:max(1,min(int(top),10))]]

    def read_source(self, path, start_line=1, end_line=180, ref=None):
        sha = self.resolve_commit(ref)
        lines = self._path(path).read_text(encoding='utf-8').splitlines()
        lo,hi = int(start_line),int(end_line)
        if lo < 1 or hi < lo or hi-lo > 200:
            raise ValueError('Invalid line range')
        selected = [{'line':i+1,'text':lines[i]} for i in range(lo-1,min(hi,len(lines)))]
        return {'path':path,'commit':sha,'lines':selected,'url':self.url(path,lo,min(hi,len(lines)),sha)}

    def get_issue(self, number):
        return {'error':'No GitHub issue corpus is provided in this fixture'}

    def url(self, path, start, end, commit):
        return f'fixture://{commit}/{path}#L{start}-L{end}'
