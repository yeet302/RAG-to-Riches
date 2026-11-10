"""
Incident Copilot orchestrator API.

Run:  uvicorn main:app --reload --port 8000     (from the orchestrator/ folder)

Endpoints
  GET  /api/health
  GET  /api/incidents          incidents for the user's team (home screen)
  POST /api/ask                {question, incident_id?, conversation_id?} -> CopilotResponse
  POST /api/feedback           {question, step_index, worked}  ("Did this work?")
  GET  /api/route?q=...        just the routing plan, for debugging

If frontend/dist exists (after `npm run build`), it is served at /.
"""
from __future__ import annotations

import asyncio
import logging
import json
from contextlib import suppress
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from orchestrator import AGENTS, SETTINGS, investigate
from suggestions import SuggestionContext, followup_questions
from query_analysis import analyze_question
from accuracy import AccuracyReview, AccuracyStore
from schemas import AskRequest, CopilotResponse, Feedback, Incident, RoutingMode

log = logging.getLogger("orchestrator")
app = FastAPI(title="Incident Copilot Orchestrator", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

ACCURACY = AccuracyStore(Path(os.getenv("ACCURACY_DB_PATH", str(Path(__file__).parent / "results" / "accuracy.sqlite3"))))

FEEDBACK: list[Feedback] = []  # in-memory local feedback


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": SETTINGS.mode,
            "source_note": ("Offline walkthrough: repository docs/code and bundled historical logs. No live Azure diagnosis."
                            if os.getenv("SHOWCASE_OFFLINE") == "1" else "Configured specialist services" if SETTINGS.mode == "remote" else "Operator-selected local files"),
            "agents": {name: type(agent).__name__ for name, agent in AGENTS.items()},
            "routing_mode": SETTINGS.effective_routing_mode,
            "routing_fast_path": SETTINGS.routing_fast_path,
            "llm_configured": SETTINGS.llm_configured,
            "answer_mode": SETTINGS.effective_answer_mode,
            "agent_timeouts_seconds": {name: SETTINGS.timeout_for(name) for name in AGENTS}}


@app.get("/api/incidents", response_model=list[Incident])
def incidents():
    # No incident feed is connected; do not populate synthetic incidents.
    return []


@app.get("/api/suggestions")
def suggestions():
    return []


@app.post("/api/suggestions")
async def contextual_suggestions(context: SuggestionContext):
    return await followup_questions(context, SETTINGS)


@app.get("/api/route")
async def route(q: str, routing_mode: RoutingMode | None = None):
    q = q.strip()
    if not q or len(q) > 2000:
        raise HTTPException(400, "question must contain 1 to 2000 characters")
    analysis = await analyze_question(q, SETTINGS, routing_mode)
    return {**analysis.model_dump(), "stage1_parallel": analysis.stage1}


@app.post("/api/ask", response_model=CopilotResponse)
async def ask(req: AskRequest):
    return await run_ask(req)


async def run_ask(req: AskRequest, on_progress=None):
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "question is empty")
    if len(q) > 2000:
        raise HTTPException(400, "question is too long (max 2000 characters)")
    response = await investigate(q, incident_id=req.incident_id,
                             conversation_id=req.conversation_id, routing_mode=req.routing_mode,
                             answer_mode=req.answer_mode, **({"on_progress": on_progress} if on_progress else {}))
    try:
        await asyncio.to_thread(ACCURACY.record, response,
                               "Local source search" if SETTINGS.mode == "local" else "Configured services")
        response.accuracy_recorded = True
    except (OSError, sqlite3.Error):
        log.error("request_id=%s accuracy recording failed", response.request_id)
    return response


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest):
    if not req.question.strip() or len(req.question.strip()) > 2000:
        raise HTTPException(400, "question must contain 1 to 2000 characters")

    async def events():
        queue = asyncio.Queue()

        async def run():
            try:
                response = await run_ask(req, lambda event: queue.put_nowait({"type": "progress", **event}))
                queue.put_nowait({"type": "result", "response": response.model_dump(mode="json")})
            except Exception:
                log.exception("Streaming investigation failed")
                queue.put_nowait({"type": "error", "message": "The investigation could not finish. Please try again."})

        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield json.dumps({"type": "heartbeat"}) + "\n"
                    continue
                yield json.dumps(event) + "\n"
                if event["type"] in {"result", "error"}:
                    break
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/accuracy")
def accuracy_results(limit: int = Query(default=100, ge=1, le=200)):
    return ACCURACY.list(limit)


@app.patch("/api/accuracy/{request_id}")
def accuracy_review(request_id: str, review: AccuracyReview):
    result = ACCURACY.review(request_id, review)
    if result is None:
        raise HTTPException(404, "Investigation result not found")
    return result


@app.get("/api/accuracy.csv")
def accuracy_export():
    return Response(ACCURACY.export(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="accuracy-results.csv"'})



@app.post("/api/feedback")
def feedback(fb: Feedback):
    FEEDBACK.append(fb)
    log.info("feedback: step %s worked=%s for %r", fb.step_index, fb.worked, fb.question)
    return {"recorded": True, "count": len(FEEDBACK)}


# ---- serve the built front end (optional) ----
DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        return FileResponse(DIST / "index.html")
