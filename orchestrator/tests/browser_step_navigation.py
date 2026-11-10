"""Browser-only fixtures verify step review and routing diagrams; no models called."""
import argparse
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


def fixture(stage1=None, stage2=None, fast=False):
    stage1 = ["logs", "docs"] if stage1 is None else stage1
    stage2 = ["code"] if stage2 is None else stage2
    roles = stage1 + stage2
    steps = [dict(title=f"Check fixture step {i}", why=f"Reason for fixture step {i}",
                  command=f"echo fixture-{i}", expect=f"Expected fixture result {i}",
                  risk="low",kind="verify",evidence_refs=["fixture.log:L1"]) for i in range(1,4)]
    return dict(kind="diagnosis",question="Inspect the fixture incident",answer="Controlled browser fixture; no actual agent or remediation was run.",
        confidence=None,confidence_reasons=[],evidence=[dict(agent="logs",source_type="log",summary="Fixture evidence",ref="fixture.log:L1")],
        sources_unavailable=[],steps=steps,agents_used=roles,trace=[],suggestions=[],errors=[],pipeline_status="ok",demo=False,
        accuracy_recorded=True,analysis=dict(source="keyword" if fast else "llm",fast_path=fast,intent="incident",summary="Fixture routing",
            reason="Controlled routing data for browser tests",stage1=stage1,stage2=stage2,
            assignments=[dict(agent=role,stage=stage,question="Fixture task",task="Inspect test sources")
                         for stage,names in [(1,stage1),(2,stage2)] for role in names]),
        synthesis=dict(source="evidence",citations=[],warning=None,context_truncated=False,latency_ms=0),
        performance=dict(total_ms=150,slowest_step="agent:logs",recommendations=["Inspect the slowest source request"],
            steps=[dict(name="routing",latency_ms=0.2 if fast else 1.5,parallel=False)] +
                  [dict(name="agent:"+role,latency_ms={"logs":80,"docs":60,"code":50}[role],parallel=len(names)>1)
                   for stage,names in [(1,stage1),(2,stage2)] for role in names]))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts",type=Path,default=ROOT/"diagnostics/step-navigation")
    args=parser.parse_args()
    args.artifacts.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(("127.0.0.1",0),partial(QuietHandler,directory=str(ROOT/"frontend/dist")))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    errors,feedback,checks=[],[],[]
    started=time.perf_counter()
    try:
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(headless=True)
            page=browser.new_page(viewport={"width":1280,"height":1000})
            page.on("pageerror",lambda error: errors.append(str(error)))
            page.route("**/api/health",lambda route:route.fulfill(json=dict(status="ok",mode="remote",agents={},routing_mode="llm",llm_configured=True,answer_mode="evidence",source_note="Browser test fixtures; no live agents.")))
            page.route("**/api/incidents",lambda route:route.fulfill(json=[]))
            def record_feedback(route):
                feedback.append(route.request.post_data_json)
                route.fulfill(json={"recorded":True})
            page.route("**/api/feedback",record_feedback)
            def show(response):
                page.unroute("**/api/ask/stream")
                page.route("**/api/ask/stream",lambda route:route.fulfill(content_type="application/x-ndjson", body=json.dumps({"type": "result", "response": response}) + "\n"))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.get_by_role("textbox").first.fill("Inspect the fixture incident")
                page.locator("form.composer").get_by_role("button",name="Investigate",exact=True).click()
                page.get_by_role("region",name="Agent routing",exact=True).wait_for()
            for mode in ['local', 'remote']:
                page.unroute("**/api/health")
                page.route("**/api/health",lambda route, request, mode=mode:route.fulfill(json=dict(status="ok",mode=mode,agents={},routing_mode="llm",llm_configured=True,answer_mode="evidence",source_note="Browser test fixtures; no live agents.")))
                scored = fixture()
                scored.update(confidence=75, confidence_band="medium", confidence_reasons=["Controlled source support"])
                show(scored)
                expect(page.locator('#routing-mode, #answer-mode')).to_have_count(0)
                expect(page.locator('.confidence .pct')).to_have_text('75%')
                page.get_by_role('button', name='Why 75%?', exact=True).click()
                expect(page.get_by_text('Controlled source support', exact=True)).to_be_visible()
                diagram = page.get_by_role('region', name='Agent routing', exact=True)
                expect(diagram).to_be_visible()
                diagram.screenshot(path=str(args.artifacts / f'routing-scored-{mode}.png'))
                checks.append(f"{mode}: server-selected modes, routing diagram, and evidence confidence score")
            show(fixture())
            panel=page.get_by_role("region",name="What to try",exact=True)
            previous=panel.get_by_role("button",name="Previous step",exact=True)
            next_step=panel.get_by_role("button",name="Next step",exact=True)
            expect(previous).to_be_disabled()
            expect(panel.get_by_text("Step 1 of 3",exact=True)).to_be_visible()
            next_step.click()
            expect(panel.get_by_text("Reason for fixture step 2",exact=False)).to_be_visible()
            previous.click()
            expect(panel.get_by_text("Reason for fixture step 1",exact=False)).to_be_visible()
            assert not feedback
            checks.append("Previous/Next review without recording outcomes; first boundary disabled")
            with page.expect_response(lambda response:response.url.endswith("/api/feedback")):
                panel.get_by_role("button",name="No, next step",exact=True).click()
            expect(panel.get_by_text("Step 2 of 3",exact=True)).to_be_visible()
            previous.click()
            expect(panel.locator("li.step").nth(0)).to_have_class("step active failed")
            assert len(feedback)==1 and feedback[0]["step_index"]==0 and feedback[0]["worked"] is False
            next_step.click()
            next_step.click()
            expect(next_step).to_be_disabled()
            with page.expect_response(lambda response:response.url.endswith("/api/feedback")):
                panel.get_by_role("button",name="Yes",exact=True).click()
            expect(panel.get_by_text("A step was marked as worked.",exact=False)).to_be_visible()
            previous.click()
            expect(panel.get_by_text("Step 2 of 3",exact=True)).to_be_visible()
            expect(panel.locator("li.step").nth(2)).to_have_class("step worked")
            assert len(feedback)==2
            checks.append("Recorded failures and success survive backward navigation; last boundary disabled")
            diagram=page.get_by_role("region",name="Agent routing",exact=True)
            assert diagram.get_by_role("group",name="Stage 1: parallel",exact=True).locator(".route-agent").count()==2
            assert diagram.get_by_role("group",name="Stage 2: one agent",exact=True).locator(".route-agent").count()==1
            expect(diagram.get_by_text("Stage 2 receives Stage 1 findings before it starts.",exact=True)).to_be_visible()
            page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
            diagram.screenshot(path=str(args.artifacts/"routing-staged-desktop.png"))
            panel.screenshot(path=str(args.artifacts/"previous-step-desktop.png"))
            checks.append("Routing diagram preserves parallel Stage 1 and dependent Stage 2")
            show(fixture())
            panel=page.get_by_role("region",name="What to try",exact=True)
            for index in range(3):
                with page.expect_response(lambda response:response.url.endswith("/api/feedback")):
                    panel.get_by_role("button",name="No, next step" if index<2 else "No",exact=True).click()
            expect(panel.get_by_text("All steps were marked as not working.",exact=False)).to_be_visible()
            panel.get_by_role("button",name="Previous step",exact=True).click()
            expect(panel.get_by_text("Reason for fixture step 2",exact=False)).to_be_visible()
            expect(panel.get_by_text("All steps were marked as not working.",exact=False)).to_be_visible()
            assert len(feedback)==5
            checks.append("Previous works after all steps fail and retains outcome history")
            show(fixture(["logs","docs","code"],[]))
            diagram=page.get_by_role("region",name="Agent routing",exact=True)
            assert diagram.get_by_role("group",name="Stage 1: parallel",exact=True).locator(".route-agent").count()==3
            assert diagram.get_by_role("group",name="Stage 2: one agent",exact=True).count()==0
            expect(diagram.get_by_text("Agents in the same stage run together.",exact=True)).to_be_visible()
            diagram.screenshot(path=str(args.artifacts/"routing-parallel-desktop.png"))
            checks.append("Three truly parallel agents appear together in Stage 1")
            page.set_viewport_size({"width":390,"height":844})
            diagram.screenshot(path=str(args.artifacts/"routing-parallel-mobile.png"))
            page.get_by_role("region",name="What to try",exact=True).screenshot(path=str(args.artifacts/"previous-step-mobile.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            checks.append("Mobile routing and navigation fit viewport")
            show(fixture(["logs"],[],True))
            diagram=page.get_by_role("region",name="Agent routing",exact=True)
            expect(diagram.get_by_text("Fast path",exact=True)).to_be_visible()
            expect(diagram.get_by_text("No planning model call",exact=True)).to_be_visible()
            assert diagram.get_by_role("group",name="Stage 1: one agent",exact=True).locator(".route-agent").count()==1
            checks.append("Single-agent fast-path route does not imply parallel or model planning")
            long_response=fixture(["logs"],[],True)
            long_response["answer"]="First paragraph.\n\nLong source token: " + "answer_token_"*100
            long_response["evidence"][0].update(summary="summary_token_"*30,
                ref="repository/"+"very_long_path_"*30+".log:L1-L100",
                snippet="\n".join("log_entry_"*60 for _ in range(80)))
            long_response["steps"][0]["command"]="echo " + "long_command_token_"*60
            long_response["steps"][0]["evidence_refs"]=[long_response["evidence"][0]["ref"]]
            for width in [1280,390]:
                page.set_viewport_size({"width":width,"height":1000 if width>600 else 844})
                show(long_response)
                page.get_by_role("button",name="Show lines",exact=True).first.click()
                excerpt=page.get_by_role("region",name="Source excerpt",exact=True)
                expect(excerpt).to_be_visible()
                assert excerpt.evaluate("element => element.scrollHeight > element.clientHeight")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                overflowing=page.locator(".evidence-row, .snippet, .command, .answer-text, .ref").evaluate_all(
                    "elements => elements.filter(element => element.scrollWidth > element.clientWidth + 2).map(element => element.className)")
                assert not overflowing,overflowing
                page.locator(".evidence-row").first.screenshot(path=str(args.artifacts/f"long-evidence-{width}.png"))
                checks.append(f"Long evidence, source paths, answers, and commands fit cards at {width}px")
            citations=fixture()
            refs=["OTelLogs/2026-11-03T05:18:45.615941+00:00/ad/trace-id", "docs/guide.md:L1", "log[server]/entry:L2", "src/ad/AdService.java@abc123:L162-L230"]
            citations["evidence"]=[dict(agent="logs" if i<3 else "code",source_type="log" if i<3 else "code",summary=f"Source summary {i+1}",ref=ref,snippet=f"Source excerpt {i+1} with [injected-test-failure]") for i,ref in enumerate(refs)]
            citations["answer"]=f"The logs contain UNAVAILABLE [{refs[0]}].\n\nThe code path is recorded [{refs[3]}], with another reference [{refs[3]}]. The literal label [injected-test-failure] is unchanged."
            for width in [1280,390]:
                page.set_viewport_size({"width":width,"height":1000 if width>600 else 844})
                show(citations)
                answer=page.locator(".cited-answer")
                assert answer.locator("p").count()==2
                assert answer.locator(".source-citation").count()==3
                assert refs[0] not in answer.inner_text() and refs[3] not in answer.inner_text()
                assert "[injected-test-failure]" in answer.inner_text()
                assert page.locator(".evidence-row").count()==3
                citation=answer.get_by_role("button",name="Show source 4",exact=True).first
                citation.focus();citation.press("Enter")
                expect(page.locator(".evidence-row")).to_have_count(4)
                selected=page.locator(".evidence-row.selected-source")
                expect(selected.locator(".ref")).to_have_text(refs[3])
                expect(selected.get_by_role("region",name="Source excerpt")).to_have_text("Source excerpt 4 with [injected-test-failure]")
                selected.get_by_role("button",name="Hide lines",exact=True).click()
                citation.click()
                expect(selected.get_by_role("region",name="Source excerpt")).to_be_visible()
                answer.get_by_role("button",name="Show source 1",exact=True).click()
                page.get_by_role("button",name="Show less",exact=True).click()
                expect(page.locator(".evidence-row")).to_have_count(3)
                citation.click()
                expect(page.locator(".evidence-row")).to_have_count(4)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.locator(".card.diagnosis").screenshot(path=str(args.artifacts/f"compact-citations-{width}.png"))
                checks.append(f"Numbered citations reveal original hidden sources and preserve literal labels at {width}px")
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    summary=dict(status="passed",scope="Controlled browser UI fixtures; no subagent/model calls or production feedback",elapsed_ms=round((time.perf_counter()-started)*1000,3),checks=checks,browser_errors=errors,explicit_feedback_calls=len(feedback))
    (args.artifacts/"results.json").write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))


if __name__=="__main__":
    main()
