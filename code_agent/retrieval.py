"""Only the approved OpenTelemetry Demo repository; any existing Git ref is allowed.

All GitHub code references are resolved to immutable commit SHAs for attribution.
"""

import base64
import re
import requests
from collections import OrderedDict
import threading
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from .config import Settings, APPROVED_REPOSITORY
from .budget import azure_options, network_timeout, perform


class CodeRetriever:
    def __init__(self, settings: Settings):
        if settings.repo != APPROVED_REPOSITORY:
            raise ValueError("Repository not approved")
        self.settings = settings
        self._immutable_cache = OrderedDict()
        self._cache_lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": "rag-to-riches-code-agent",
            }
        )
        if settings.github_token:
            self.session.headers["Authorization"] = f"Bearer {settings.github_token}"
        self.search = SearchClient(
            settings.search_endpoint, settings.search_index, DefaultAzureCredential()
        )

    def _get(self, endpoint, **kwargs):
        params = kwargs.get("params") or {}
        # Only commit-pinned trees and contents are stable enough to reuse.
        # Branch resolution, issues and other mutable responses stay uncached.
        immutable = (endpoint.startswith("git/trees/") and re.fullmatch(r"[0-9a-fA-F]{40}", endpoint.rsplit("/", 1)[-1])) or (
            endpoint.startswith("contents/") and re.fullmatch(r"[0-9a-fA-F]{40}", params.get("ref", "")))
        key = (endpoint, tuple(sorted(params.items())))
        if immutable:
            with self._cache_lock:
                if key in self._immutable_cache:
                    # Refresh recency, not freshness: this commit cannot change.
                    self._immutable_cache.move_to_end(key)
                    return self._immutable_cache[key]
        url = f"https://api.github.com/repos/{APPROVED_REPOSITORY}/{endpoint}".rstrip(
            "/"
        )
        r = perform("github.request", self.session.get, url,
                    timeout=network_timeout(self.settings.request_timeout_seconds), **kwargs)
        r.raise_for_status()
        result = r.json()
        if immutable:
            with self._cache_lock:
                self._immutable_cache[key] = result
                # Bound process memory while sharing verified immutable reads.
                if len(self._immutable_cache) > 64:
                    self._immutable_cache.popitem(last=False)
        return result

    def resolve_commit(self, ref=None):
        """Resolve branch, tag, or SHA to an immutable commit for a consistent investigation."""
        chosen = ref or self.settings.default_ref
        if not chosen:
            chosen = self._get("")["default_branch"]
        if (
            not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", chosen)
            or ".." in chosen
            or chosen.startswith(("/", "-"))
        ):
            raise ValueError("Invalid Git ref")
        if re.fullmatch(r"[0-9a-fA-F]{40}", chosen):
            # Tool calls already receive the investigation's pinned SHA. Their
            # actual GitHub reads verify it without another commits API request.
            return chosen.lower()
        return self._get("commits/" + chosen)["sha"]

    def list_repository_files(self, path_prefix="", limit=150, ref=None):
        if (
            path_prefix.startswith("/")
            or ".." in path_prefix.split("/")
            or "\\" in path_prefix
        ):
            raise ValueError("Invalid path prefix")
        sha = self.resolve_commit(ref)
        tree = self._get("git/trees/" + sha, params={"recursive": 1})
        paths = [
            x["path"]
            for x in tree.get("tree", [])
            if x.get("type") == "blob" and x.get("path", "").startswith(path_prefix)
        ]
        limit = max(1, min(500, int(limit)))
        return {
            "files": paths[:limit],
            "total_matching": len(paths),
            "truncated": bool(tree.get("truncated")) or len(paths) > limit,
            "commit": sha,
        }

    def search_code(self, query, top=6, ref=None):
        sha = self.resolve_commit(ref)
        top = max(1, min(10, int(top)))
        # Filter IN Azure, not after top-K. Each hit includes its source revision.
        hits = self.search.search(
            search_text=query,
            top=top,
            filter=f"commit eq '{sha}'",
            select=["path", "start_line", "end_line", "content", "commit"],
            **azure_options(self.settings.request_timeout_seconds),
        )
        return [
            {
                "path": h["path"],
                "start_line": h["start_line"],
                "end_line": h["end_line"],
                "content": h["content"][:5000],
                "commit": h["commit"],
            }
            for h in hits
        ]

    def read_source(self, path, start_line=1, end_line=180, ref=None):
        if not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
            raise ValueError("Invalid repository path")
        lo, hi = int(start_line), int(end_line)
        if lo < 1 or hi < lo or hi - lo > 200:
            raise ValueError("Invalid line range")
        sha = self.resolve_commit(ref)
        obj = self._get("contents/" + path, params={"ref": sha})
        if not isinstance(obj, dict) or obj.get("type") != "file":
            raise ValueError("Not a source file")
        if obj.get("size", 0) > 400000:
            raise ValueError("Source file too large")
        lines = base64.b64decode(obj["content"]).decode("utf-8", "replace").splitlines()
        out = [
            {"line": i + 1, "text": lines[i]}
            for i in range(lo - 1, min(hi, len(lines)))
        ]
        return {
            "path": path,
            "commit": sha,
            "lines": out,
            "url": self.url(path, lo, min(hi, len(lines)), sha),
        }

    def get_issue(self, number):
        obj = self._get("issues/" + str(int(number)))
        return {
            "number": obj["number"],
            "title": obj["title"],
            "body": (obj.get("body") or "")[:8000],
            "url": obj["html_url"],
        }

    def url(self, path, start, end, commit):
        return f"https://github.com/{APPROVED_REPOSITORY}/blob/{commit}/{path}#L{start}-L{end}"
