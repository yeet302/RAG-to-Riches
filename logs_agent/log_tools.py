from pathlib import Path
import re
from functools import lru_cache

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
                        "text": line
                    })

    return logs


def list_log_sources():
    """
    List the available log files and the number of lines in each.

    Returns:
        Dictionary mapping filename to line count.
    """

    counts = {}

    for log in load_logs():
        source = log["source"]
        counts[source] = counts.get(source, 0) + 1

    return counts


def search_logs(
    query: str,
    source: str = "",
    limit: int = 20
):
    """
    Search application logs for keywords or identifiers.

    Args:
        query: Keywords, error text, component names, IDs, etc.
        source: Optional exact filename. Leave blank to search all logs.
        limit: Maximum number of matching lines.

    Returns:
        Relevant matching log lines.
    """

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

        score = sum(
            text_lower.count(term)
            for term in terms
        )

        if score > 0:
            results.append({
                **log,
                "score": score
            })

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return results[:limit]


def get_log_context(
    source: str,
    line_number: int,
    before: int = 5,
    after: int = 5
):
    """
    Get surrounding log lines around one event.

    Args:
        source: Exact log filename.
        line_number: Target line number.
        before: Number of lines before the event.
        after: Number of lines after the event.

    Returns:
        Surrounding log lines.
    """

    path = LOG_DIR / source

    if not path.exists():
        return {
            "error": f"Unknown source: {source}"
        }

    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = [line.rstrip("\n") for line in f]

    start = max(0, line_number - 1 - before)
    end = min(len(lines), line_number + after)

    return [
        {
            "source": source,
            "line_number": i + 1,
            "text": lines[i]
        }
        for i in range(start, end)
    ]


if __name__ == "__main__":
    print(list_log_sources())

    print("\nSample errors:\n")

    for result in search_logs("ERROR", limit=5):
        print(
            result["source"],
            result["line_number"],
            result["text"]
        )