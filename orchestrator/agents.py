"""Local retrieval specialists and the common JSON-over-HTTP adapter."""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Protocol

import httpx

from schemas import AgentFinding, AgentRequest, Evidence, PipelineError
from settings import NAMES, Settings

TASKS = {
    "logs": "Search operational logs for relevant errors, events, and patterns. Cite exact sources.",
    "docs": "Search documentation, runbooks, and past incidents for existing solutions. Cite exact sources.",
    "code": "Use the supplied evidence to locate relevant code paths and configuration. Cite exact files and lines.",
}


class Agent(Protocol):
    name: str

    async def run(self, question: str, context: dict) -> AgentFinding: ...


class UnavailableAgent:
    def __init__(self, name: str, reason: str):
        self.name, self.reason = name, reason

    async def run(self, question: str, context: dict) -> AgentFinding:
        return AgentFinding(agent=self.name, status="no_access", summary=self.reason,
                            sources_unavailable=[self.reason], error=PipelineError(
                                code="AGENT_NOT_CONFIGURED", agent=self.name, stage="dispatch",
                                message=f"The {self.name} agent is not configured.",
                                resolution=f"Set {self.name.upper()}_AGENT_URL to the running service endpoint."))


class HTTPAgent:
    """POST exactly one versioned request; validate the returned specialist identity."""
    def __init__(self, name: str, url: str, timeout: float = 20, token: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.name, self.url, self.timeout, self.token = name, url, timeout, token
        self.transport = transport

    async def run(self, question: str, context: dict) -> AgentFinding:
        request = AgentRequest(
            request_id=context["request_id"], question=question,
            original_question=context.get("original_question", question),
            task=context.get("task", TASKS[self.name]),
            intent=context["intent"], incident_id=context.get("incident_id"),
            conversation_id=context.get("conversation_id"),
            evidence_bundle=context.get("evidence_bundle", {}),
        )
        headers = {"X-Request-ID": request.request_id}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        # Slow source investigations can need a long read budget, while a dead
        # service should still fail promptly during connection establishment.
        timeout = httpx.Timeout(self.timeout, connect=min(10.0, self.timeout), pool=min(10.0, self.timeout))
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport,
                                     follow_redirects=False, trust_env=False) as client:
            response = await client.post(self.url, json=request.model_dump(), headers=headers)
            response.raise_for_status()
            finding = AgentFinding.model_validate(response.json())
        if finding.agent != self.name:
            raise ValueError("Agent response identity does not match requested specialist")
        return finding


# Local retrieval is intentionally bounded and read-only. No shell commands or
# instructions found in retrieved text are executed.
SUFFIXES = {
    "docs": {".md", ".rst", ".txt", ".adoc"},
    "logs": {".log", ".jsonl", ".ndjson", ".txt"},
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cs",
             ".c", ".cpp", ".h", ".yaml", ".yml", ".toml", ".json", ".sh"},
}
SKIP_DIRS = {"node_modules", "venv", "env", "dist", "build", "__pycache__", "tests"}
SKIP_FILES = {"demo_data.py", "package-lock.json", "pnpm-lock.yaml", "uv.lock"}
STOP_WORDS = set("a an and are as at be by can could do does for from how i in is it me my of on or "
                 "please show tell that the their then there this to use was what when where which why "
                 "with would you agent agents docs documentation code codebase logs log find search".split())


NORMAL_FORMS = {"routing": "route", "routes": "route", "routed": "route", "requests": "request",
                "errors": "error", "timeouts": "timeout", "retries": "retry", "functions": "function"}


def terms(text: str) -> set[str]:
    return {NORMAL_FORMS.get(t.lower(), t.lower()) for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]{3}", text)
            if len(t) > 1 and t.lower() not in STOP_WORDS}


class LocalSearchAgent:
    """Search an operator-selected corpus, returning real file/line evidence."""
    def __init__(self, name: str, root: Path, timeout: float = 20):
        self.name, self.root, self.timeout = name, Path(root).resolve(), timeout

    async def run(self, question: str, context: dict) -> AgentFinding:
        # Keep filesystem work off the ASGI event loop; the worker has its own deadline.
        return await asyncio.to_thread(self._search, question, context)

    def _search(self, question: str, context: dict) -> AgentFinding:
        if not self.root.is_dir():
            return AgentFinding(agent=self.name, status="no_access",
                                summary=f"{self.name} corpus directory is unavailable.",
                                sources_unavailable=[f"{self.name} corpus directory"])
        query = terms(question)
        # Stage 1 may supply exact symbols that the user's question did not contain.
        extra = set()
        if self.name == "code":
            for item in context.get("evidence_bundle", {}).get("evidence", [])[:8]:
                extra.update(terms(item.get("summary", "")))
        extra -= query
        hits, unavailable = [], []
        scanned, truncated = 0, False
        deadline = time.monotonic() + self.timeout
        def walk_error(error):
            unavailable.append("A corpus directory could not be read")
        for directory, dirs, files in os.walk(self.root, followlinks=False, onerror=walk_error):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
                             and not (Path(directory) / d).is_symlink())
            for filename in sorted(files):
                if scanned >= 500 or time.monotonic() >= deadline:
                    truncated = True
                    break
                path = Path(directory) / filename
                if (filename.startswith(".") or filename in SKIP_FILES or path.is_symlink()
                        or path.suffix.lower() not in SUFFIXES[self.name]):
                    continue
                scanned += 1
                try:
                    if not path.resolve().is_relative_to(self.root):
                        continue
                    with path.open("rb") as source:
                        raw = source.read(1_000_001)
                    if len(raw) > 1_000_000:
                        unavailable.append(f"{path.relative_to(self.root)} exceeds the 1 MB search limit")
                        continue
                    lines = raw.decode("utf-8").splitlines()
                except (OSError, UnicodeError):
                    unavailable.append(f"{path.relative_to(self.root)} could not be read as UTF-8")
                    continue
                relative = path.relative_to(self.root).as_posix()
                best = None
                path_terms = terms(relative)
                for index, line in enumerate(lines):
                    # API examples often repeat the exact user query without answering it.
                    if self.name == "docs" and re.search(r"[\"']question[\"']\s*:", line):
                        continue
                    line_terms = terms(line)
                    score = 3 * len(query & line_terms) + min(2, len(extra & line_terms))
                    # The question must match; prior context alone cannot make an unrelated file relevant.
                    if not query & (line_terms | path_terms):
                        continue
                    score += len(query & path_terms)
                    if self.name == "docs" and line.lstrip().startswith("#"):
                        score *= 0.5  # Prefer explanatory text to a matching document title.
                    if best is None or score > best[0]:
                        best = (score, index)
                if best:
                    score, index = best
                    end = min(len(lines), index + 3)
                    ref = f"{relative}:L{index + 1}-L{end}"
                    snippet = "\n".join(lines[index:end])[:1200]
                    evidence = Evidence(source_type={"docs": "doc", "logs": "log", "code": "code"}[self.name],
                                        agent=self.name, summary=lines[index].strip()[:300] or relative,
                                        ref=ref, snippet=snippet)
                    hits.append((score, relative, evidence))
            if truncated:
                break
        if truncated:
            unavailable.append("Local search stopped at its 500-file or time limit; results may be incomplete")
        evidence = [hit[2] for hit in sorted(hits, key=lambda h: (-h[0], h[1]))[:4]]
        return AgentFinding(
            agent=self.name, status="ok" if evidence else "no_evidence",
            summary=f"Found {len(evidence)} matching {self.name} source(s)." if evidence
                    else f"No matching {self.name} evidence in the searched files.",
            evidence=evidence, sources_searched=[f"{self.name} corpus ({scanned} files scanned)"],
            sources_unavailable=list(dict.fromkeys(unavailable)),
            # Keyword matches establish relevance, not a verified incident cause.
            confidence=0.4 if evidence else 0,
        )


def build_agents(settings: Settings) -> dict[str, Agent]:
    registry = {}
    roots = {"docs": settings.docs_root, "code": settings.code_root, "logs": settings.logs_root}
    for name in NAMES:
        if name in settings.agent_urls:
            registry[name] = HTTPAgent(name, settings.agent_urls[name], settings.timeout_for(name),
                                       settings.agent_tokens.get(name))
        elif settings.mode == "remote" or roots[name] is None:
            registry[name] = UnavailableAgent(name, f"{name} agent has no configured endpoint or corpus")
        else:
            registry[name] = LocalSearchAgent(name, roots[name], settings.timeout_for(name))
    return registry
