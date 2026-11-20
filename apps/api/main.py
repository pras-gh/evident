"""Evident Memory Engine API.

Read-only over the memory schema. Every endpoint that returns a claim also
returns the citation behind it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from evident_ai.gateway import AllProvidersFailed, UnknownOrganization, UsageLimitExceeded
from evident_ai.gateway.logs import configure_logging

from .routers import (ai_usage, analysis, cards, documents, evidence, graph, ingest,
                      memory, search, timeline)

API_VERSION = "1.0.0"

app = FastAPI(
    title="Evident Memory Engine",
    version=API_VERSION,
    summary="Structured company intelligence from SEC filings.",
    description=(
        "Every response that makes a claim carries the document, page and "
        "paragraph that supports it."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(ai_usage.router, prefix="/v1")
app.include_router(analysis.router, prefix="/v1")
app.include_router(cards.router, prefix="/v1")
app.include_router(documents.router, prefix="/v1")
app.include_router(evidence.router, prefix="/v1")
app.include_router(graph.router, prefix="/v1")
app.include_router(ingest.router, prefix="/v1")
app.include_router(memory.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")
app.include_router(timeline.router, prefix="/v1")


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness plus a real database round-trip — a health check that does not
    touch the database only tells you the process is running."""
    from .deps import factory
    try:
        async with factory()() as db:
            await db.execute(text("select 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded",
            "version": API_VERSION, "database": db_ok}


# ------------------------------------------------------------------ AI errors
# One response shape for every endpoint that calls the gateway.

configure_logging()


@app.exception_handler(UsageLimitExceeded)
async def _usage_limit(request: Request, exc: UsageLimitExceeded) -> JSONResponse:
    headers = {}
    if exc.resets_at:
        wait = (datetime.fromisoformat(exc.resets_at) - datetime.now(timezone.utc)).total_seconds()
        headers["Retry-After"] = str(max(1, int(wait)))
    return JSONResponse(status_code=429, content=exc.to_json(), headers=headers)


@app.exception_handler(AllProvidersFailed)
async def _ai_unavailable(request: Request, exc: AllProvidersFailed) -> JSONResponse:
    # Provider detail stays in the logs; the caller gets the request id to quote.
    return JSONResponse(status_code=503, content={
        "error": "ai_unavailable", "request_id": str(exc.request_id),
        "message": "No AI provider could answer right now. Try again shortly."})


@app.exception_handler(UnknownOrganization)
async def _unknown_org(request: Request, exc: UnknownOrganization) -> JSONResponse:
    return JSONResponse(status_code=403, content={"error": "unknown_organization"})
