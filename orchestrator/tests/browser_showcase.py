"""Optional real-browser walkthrough against a running showcase.py server.

Run: .venv/bin/python tests/browser_showcase.py
Requires: pip install playwright && python -m playwright install chromium
"""
import argparse
import csv
import io
import json
from pathlib import Path
import time
import urllib.request

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--artifacts", type=Path, default=Path("../diagnostics/showcase"))
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    errors = []
    results = []
    started = time.perf_counter()
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        for index, (question, agents) in enumerate([
            ("Where is the investigate function defined?", ["code"]),
            ("How does the orchestrator route requests?", ["docs"]),
            ("Show ad service warning logs", ["logs"]),
            ("The ad service is returning errors. Why?", ["logs", "docs", "code"]),
        ]):
            page.goto(args.url)
            page.get_by_text("Offline walkthrough:", exact=False).wait_for()
            with page.expect_response(lambda r: r.url.endswith("/api/ask/stream") and r.request.method == "POST") as pending:
                page.get_by_role("button", name=question, exact=True).click()
            response = next(event["response"] for line in pending.value.text().splitlines()
                            if (event := json.loads(line)).get("type") == "result")
            assert response["agents_used"] == agents
            assert response["accuracy_recorded"]
            assert response["evidence"] and response["performance"]["steps"]
            if index == 0:
                assert response["evidence"][0]["summary"].startswith("async def investigate")
            page.get_by_text("Saved to accuracy results", exact=False).wait_for()
            page.get_by_text("Slowest step", exact=True).first.wait_for()
            if index == 0:
                page.get_by_text("Ideas to speed up the slowest step", exact=True).click()
                page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
                page.screenshot(path=str(args.artifacts / "investigation-desktop.png"), full_page=True)
            results.append({"question": question, "request_id": response["request_id"],
                            "agents": agents, "time_ms": response["performance"]["total_ms"],
                            "slowest_step": response["performance"]["slowest_step"]})
        page.get_by_role("button", name="Accuracy results", exact=True).click()
        page.get_by_role("heading", name="Accuracy results", exact=True).wait_for()
        page.get_by_role("button", name=results[0]["question"], exact=True).first.click()
        page.get_by_label("Bug or case name", exact=True).fill("DEMO-CODE-LOOKUP")
        page.get_by_label("Expected cause", exact=True).fill("Source lookup: locate investigate(); no runtime incident asserted.")
        page.get_by_label("Verification notes", exact=True).fill(
            "Browser walkthrough verified code-only routing, persisted result, timings, and CSV export. Diagnosis correctness and remediation have not been verified.")
        page.get_by_role("button", name="Save review", exact=True).click()
        page.get_by_text("Review saved.", exact=False).wait_for()
        with page.expect_download() as download:
            page.get_by_role("link", name="Download CSV", exact=True).click()
        export = args.artifacts / "demo-accuracy-results.csv"
        download.value.save_as(str(export))
        rows = list(csv.DictReader(io.StringIO(export.read_text())))
        record = next(row for row in rows if row["request id"] == results[0]["request_id"])
        assert record["bug"] == "DEMO-CODE-LOOKUP"
        assert record["correct?"] == "Not reviewed" and record["fix worked?"] == "Not tested"
        assert all(field in record for field in ["bug", "expected cause", "agent answer", "correct?", "fix worked?", "time taken"])
        page.screenshot(path=str(args.artifacts / "accuracy-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(args.artifacts / "accuracy-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.reload()
        page.get_by_role("button", name="Accuracy results", exact=True).click()
        page.get_by_role("button", name="DEMO-CODE-LOOKUP", exact=True).first.wait_for()
        assert not errors, errors
        browser.close()
    summary = {"status": "passed", "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
               "cases": results, "browser_errors": errors,
               "checks": ["plain-language routing", "real local evidence", "step timing display", "automatic recording", "human review persistence", "CSV download", "mobile viewport", "reload persistence"]}
    (args.artifacts / "browser-results.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
