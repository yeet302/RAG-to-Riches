"""
Legacy OpenStack flat-file log helpers.

Isolated (not deleted) during the pivot to the LogConnector abstraction
and OpenTelemetry Demo Ad Service logs. Prefer connectors.LocalLogConnector
for new work. These helpers load every *.log under data/, including the
original OpenStack corpora.
"""

from functools import lru_cache
from pathlib import Path
import re

LOG_DIR = Path("data")


@lru_cache(maxsize=1)
def load_logs():
    logs = []

    for path in sorted(LOG_DIR.glob("*.log")):
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line_number, line in enumerate(f, start=1):
                line = line.rstrip("\n")

                if line.strip():
                    logs.append({
                        "source": path.name,
                        "line_number": line_number,
                        "text": line,
                    })

    return logs


def list_log_sources():
    """List available log files and the number of lines in each."""
    counts = {}

    for log in load_logs():
        source = log["source"]
        counts[source] = counts.get(source, 0) + 1

    return counts


def search_logs(query: str, source: str = "", limit: int = 20):
    """Keyword search across raw OpenStack-style log lines."""
    terms = [
        term.lower()
        for term in re.findall(r"[A-Za-z0-9_.:/-]+", query)
        if len(term) > 1
    ]

    results = []

    for log in load_logs():
        if source and log["source"] != source:
            continue

        text_lower = log["text"].lower()
        score = sum(text_lower.count(term) for term in terms)

        if score > 0:
            results.append({**log, "score": score})

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def get_log_context(source: str, line_number: int, before: int = 5, after: int = 5):
    """Return surrounding raw lines around a filename + line number."""
    path = LOG_DIR / source

    if not path.exists():
        return {"error": f"Unknown source: {source}"}

    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = [line.rstrip("\n") for line in f]

    start = max(0, line_number - 1 - before)
    end = min(len(lines), line_number + after)

    return [
        {
            "source": source,
            "line_number": i + 1,
            "text": lines[i],
        }
        for i in range(start, end)
    ]
