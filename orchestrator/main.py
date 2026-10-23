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
from orchestrator import AGENTS, SETTINGS, investigate
from query_analysis import analyze_question
from schemas import AskRequest, CopilotResponse, Feedback, Incident, RoutingMode

log = logging.getLogger("orchestrator")
app = FastAPI(title="Incident Copilot Orchestrator", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

FEEDBACK: list[Feedback] = []  # in-memory for the demo


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": SETTINGS.mode,
            "agents": {name: type(agent).__name__ for name, agent in AGENTS.items()},
            "routing_mode": SETTINGS.routing_mode,
            "llm_configured": SETTINGS.llm_configured,
            "answer_mode": SETTINGS.effective_answer_mode}


@app.get("/api/incidents", response_model=list[Incident])
def incidents():
    return INCIDENTS if SETTINGS.mode == "demo" else []


@app.get("/api/suggestions")
def suggestions():
    return SUGGESTED_QUESTIONS if SETTINGS.mode == "demo" else [
        "How does the orchestrator route requests?", "Where is classify defined in the code?",
    ]


@app.get("/api/route")
async def route(q: str, routing_mode: RoutingMode | None = None):
    q = q.strip()
    if not q or len(q) > 2000:
        raise HTTPException(400, "question must contain 1 to 2000 characters")
    analysis = await analyze_question(q, SETTINGS, routing_mode)
    return {**analysis.model_dump(), "stage1_parallel": analysis.stage1}


@app.post("/api/ask", response_model=CopilotResponse)
async def ask(req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "question is empty")
    if len(q) > 2000:
        raise HTTPException(400, "question is too long (max 2000 characters)")
    return await investigate(q, incident_id=req.incident_id,
                             conversation_id=req.conversation_id, routing_mode=req.routing_mode,
                             answer_mode=req.answer_mode)


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
