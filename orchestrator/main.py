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

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from demo_data import INCIDENTS, SUGGESTED_QUESTIONS
from orchestrator import investigate
from router import classify
from schemas import AskRequest, CopilotResponse, Feedback, Incident

log = logging.getLogger("orchestrator")
app = FastAPI(title="Incident Copilot Orchestrator", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

FEEDBACK: list[Feedback] = []  # in-memory for the demo


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": "demo"}


@app.get("/api/incidents", response_model=list[Incident])
def incidents():
    return INCIDENTS


@app.get("/api/suggestions")
def suggestions():
    return SUGGESTED_QUESTIONS


@app.get("/api/route")
def route(q: str):
    p = classify(q)
    return {"intent": p.intent, "stage1_parallel": p.stage1, "stage2": p.stage2, "reason": p.reason}


@app.post("/api/ask", response_model=CopilotResponse)
async def ask(req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "question is empty")
    if len(q) > 2000:
        raise HTTPException(400, "question is too long (max 2000 characters)")
    log.info("ask: %s", q)
    return await investigate(q)


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
