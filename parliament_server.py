"""
parliament_server.py — AgentParliament as a production async HTTP service.

Exposes the full parliament deliberation pipeline via REST + WebSocket.
Suitable for multi-tenant deployments, CI/CD integration, external tooling,
and dashboard connectivity.

Endpoints:
  POST   /v1/deliberate              — submit a task
  GET    /v1/audit/{session_id}      — retrieve session audit trail
  GET    /v1/audit/verify/full       — verify chain integrity
  GET    /v1/memory/search           — semantic memory query
  GET    /v1/stats                   — system statistics
  GET    /health                     — liveness probe
  WS     /v1/stream                  — streaming deliberation progress

Run:
  uvicorn parliament_server:app --host 0.0.0.0 --port 8000 --workers 1

Or via Docker Compose:
  docker-compose up

Environment variables:
  ANTHROPIC_API_KEY         (required)
  PARLIAMENT_MEMORY_DIR     (default: ~/.openclaw/parliament/memory)
  PARLIAMENT_PROJECT_PATH   (default: cwd)
  PARLIAMENT_AUDIT_DB       (default: ~/.openclaw/parliament/audit.db)
  PARLIAMENT_ENABLE_RED_TEAM     (default: true)
  PARLIAMENT_ENABLE_SEMANTIC_MEM (default: true)
  PARLIAMENT_MAX_ROUNDS     (default: 3)
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from parliament_harness import ParliamentHarness
from audit_trail import AuditTrail


# ── Request / Response models ─────────────────────────────────────────────────

class DeliberateRequest(BaseModel):
    task: str
    max_rounds: Optional[int] = None


class DeliberateResponse(BaseModel):
    session_id: str
    task: str
    compute_tier: str
    winning_position: str
    block_reason: str
    explanation: str
    code: str
    files_modified: list[str]
    commands_to_run: list[str]
    rounds: int
    tokens_used: int
    red_team_threat_level: str
    formal_results: list[dict]
    timestamp: str


class AuditResponse(BaseModel):
    session_id: str
    chain_valid: bool
    chain_error: Optional[str]
    entries: list[dict]


class MemorySearchResponse(BaseModel):
    query: str
    backend: str
    results: list[dict]


# ── App state ─────────────────────────────────────────────────────────────────

_harness: Optional[ParliamentHarness] = None
_audit: Optional[AuditTrail] = None


def _build_harness() -> ParliamentHarness:
    memory_dir = os.environ.get(
        "PARLIAMENT_MEMORY_DIR",
        str(Path.home() / ".openclaw" / "parliament" / "memory"),
    )
    project_path = os.environ.get("PARLIAMENT_PROJECT_PATH", os.getcwd())
    max_rounds = int(os.environ.get("PARLIAMENT_MAX_ROUNDS", "3"))
    enable_red_team = os.environ.get("PARLIAMENT_ENABLE_RED_TEAM", "true").lower() == "true"
    enable_semantic = os.environ.get("PARLIAMENT_ENABLE_SEMANTIC_MEM", "true").lower() == "true"
    audit_db = os.environ.get(
        "PARLIAMENT_AUDIT_DB",
        str(Path.home() / ".openclaw" / "parliament" / "audit.db"),
    )

    return ParliamentHarness(
        memory_dir=memory_dir,
        project_path=project_path,
        max_debate_rounds=max_rounds,
        enable_red_team=enable_red_team,
        enable_semantic_memory=enable_semantic,
        audit_db=audit_db,
    )


def get_harness() -> ParliamentHarness:
    global _harness
    if _harness is None:
        _harness = _build_harness()
    return _harness


def get_audit() -> AuditTrail:
    global _audit
    if _audit is None:
        audit_db = os.environ.get(
            "PARLIAMENT_AUDIT_DB",
            str(Path.home() / ".openclaw" / "parliament" / "audit.db"),
        )
        _audit = AuditTrail(audit_db)
    return _audit


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_harness()
    get_audit()
    yield
    harness = get_harness()
    if hasattr(harness, "parliament"):
        harness.parliament.end_session()


app = FastAPI(
    title="AgentParliament",
    description=(
        "Self-organizing adversarial AI parliament — "
        "frontier intelligence without hardware"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat() + "Z"}


@app.get("/v1/stats")
async def stats():
    """System statistics: audit log counts, memory store counts, session info."""
    harness = get_harness()
    audit = get_audit()

    audit_stats = audit.stats()
    memory_stats = {}

    if hasattr(harness.parliament, "_semantic_memory") and harness.parliament._semantic_memory:
        memory_stats = harness.parliament._semantic_memory.stats()

    return {
        "session_id": harness.session_id,
        "audit": audit_stats,
        "semantic_memory": memory_stats,
        "red_team_enabled": harness.parliament._red_team is not None,
        "semantic_memory_enabled": harness.parliament._semantic_memory is not None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/v1/deliberate", response_model=DeliberateResponse)
async def deliberate(request: DeliberateRequest):
    """
    Submit a task to the adversarial parliament for deliberation.

    The parliament routes to the appropriate compute tier (single/dual/parliament),
    runs the constitutional debate, applies formal verification, and returns
    the verdict with full audit trail entry recorded.
    """
    harness = get_harness()
    audit = get_audit()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, harness.execute_task, request.task)

    red_team_level = "N/A"
    red_team = getattr(result, "red_team_report", None)
    if red_team:
        red_team_level = red_team.threat_level

    audit.record(
        session_id=harness.session_id,
        action="deliberate",
        actor="AgentParliament",
        outcome=result.winning_position.upper(),
        task=request.task,
        details={
            "compute_tier": result.compute_tier,
            "rounds": len(result.rounds),
            "tokens": result.total_input_tokens + result.total_output_tokens,
            "block_reason": result.block_reason,
            "red_team_threat_level": red_team_level,
            "formal_results": [
                {"tool": fr.tool, "passed": fr.passed}
                for fr in result.formal_results
            ],
        },
    )

    final_output = result.final_output or {}
    return DeliberateResponse(
        session_id=harness.session_id,
        task=request.task,
        compute_tier=result.compute_tier,
        winning_position=result.winning_position,
        block_reason=result.block_reason,
        explanation=final_output.get("explanation", ""),
        code=final_output.get("code", ""),
        files_modified=final_output.get("files_modified", []),
        commands_to_run=final_output.get("commands_to_run", []),
        rounds=len(result.rounds),
        tokens_used=result.total_input_tokens + result.total_output_tokens,
        red_team_threat_level=red_team_level,
        formal_results=[
            {
                "tool": fr.tool,
                "passed": fr.passed,
                "output": fr.output[:500],
                "duration_ms": fr.duration_ms,
            }
            for fr in result.formal_results
        ],
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


@app.get("/v1/audit/{session_id}", response_model=AuditResponse)
async def get_audit_trail(session_id: str):
    """
    Retrieve the tamper-evident audit trail for a specific session.
    Each entry includes a chain hash — verify_chain() confirms no tampering.
    """
    audit = get_audit()
    entries = audit.query_session(session_id)
    valid, error = audit.verify_chain()
    return AuditResponse(
        session_id=session_id,
        chain_valid=valid,
        chain_error=error,
        entries=entries,
    )


@app.get("/v1/audit/verify/full")
async def verify_full_chain():
    """
    Verify the cryptographic integrity of the complete audit chain.
    Returns chain_valid=true only if no entry has been modified since recording.
    """
    audit = get_audit()
    loop = asyncio.get_event_loop()
    valid, error = await loop.run_in_executor(None, audit.verify_chain)
    return {
        "chain_valid": valid,
        "chain_error": error,
        "verified_at": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/v1/memory/search", response_model=MemorySearchResponse)
async def search_memory(query: str, category: Optional[str] = None, top_k: int = 5):
    """
    Semantic search over the collective's accumulated memory.
    Returns the most relevant past experiences for a given query.
    """
    harness = get_harness()
    mem = getattr(harness.parliament, "_semantic_memory", None)
    if not mem:
        raise HTTPException(status_code=503, detail="Semantic memory not enabled")

    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        None, lambda: mem.search(query, category=category, top_k=top_k)
    )
    backend = "chromadb" if mem._use_chroma else "keyword_fallback"

    return MemorySearchResponse(
        query=query,
        backend=backend,
        results=[
            {
                "content": r.content,
                "category": r.category,
                "relevance": round(r.relevance_score, 3),
                "timestamp": r.timestamp,
                "session_id": r.session_id,
            }
            for r in results
        ],
    )


@app.websocket("/v1/stream")
async def stream_deliberation(websocket: WebSocket):
    """
    WebSocket endpoint for streaming parliament deliberation progress.
    Client sends {"task": "..."}, receives events as rounds complete.
    """
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        task = data.get("task", "").strip()
        if not task:
            await websocket.send_json({"event": "error", "message": "task is required"})
            return

        harness = get_harness()
        loop = asyncio.get_event_loop()

        await websocket.send_json({"event": "received", "task": task})

        tier = harness.parliament._classify_compute_tier(task)
        await websocket.send_json({"event": "tier_classified", "tier": tier})

        result = await loop.run_in_executor(None, harness.execute_task, task)

        red_team = getattr(result, "red_team_report", None)
        await websocket.send_json({
            "event": "complete",
            "session_id": harness.session_id,
            "winning_position": result.winning_position,
            "compute_tier": result.compute_tier,
            "rounds": len(result.rounds),
            "blocked": result.winning_position == "blocked",
            "block_reason": result.block_reason,
            "explanation": result.final_output.get("explanation", "") if result.final_output else "",
            "red_team_threat_level": red_team.threat_level if red_team else "N/A",
            "tokens_used": result.total_input_tokens + result.total_output_tokens,
        })
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"event": "error", "message": str(exc)})
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "parliament_server:app",
        host="0.0.0.0",
        port=8000,
        workers=1,
        log_level="info",
        reload=False,
    )
