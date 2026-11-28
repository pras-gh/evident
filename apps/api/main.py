"""Parallx Memory Engine API.

Read-only over the memory schema. Every endpoint that returns a claim also
returns the citation behind it.
"""
from __future__ import annotations

import functools
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from parallx_ai.gateway import AllProvidersFailed, UnknownOrganization, UsageLimitExceeded
from parallx_ai.gateway.logs import configure_logging
from parallx_db import store

from . import production
from .routers import (account, ai_usage, analysis, ask, billing, cards, documents,
                      evidence, fetches,
                      graph, ingest, memory, search, sec, sync, timeline)
from .errors import ApiError
from .routers.search import SearchUnavailable

API_VERSION = "1.0.0"

# Before anything is served: in production, a missing setting stops here.
production.enforce()

# The interactive docs describe every endpoint to anyone who asks; in
# production they are off unless API_DOCS=1.
_docs = not production.is_production() or os.environ.get("API_DOCS") == "1"

app = FastAPI(
    title="Parallx Memory Engine",
    version=API_VERSION,
    summary="Structured company intelligence from SEC filings.",
    description=(
        "Every response that makes a claim carries the document, page and "
        "paragraph that supports it."
    ),
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(account.router, prefix="/v1")
app.include_router(ai_usage.router, prefix="/v1")
app.include_router(billing.router, prefix="/v1")
app.include_router(analysis.router, prefix="/v1")
app.include_router(ask.router, prefix="/v1")
app.include_router(cards.router, prefix="/v1")
app.include_router(documents.router, prefix="/v1")
app.include_router(evidence.router, prefix="/v1")
app.include_router(fetches.router, prefix="/v1")
app.include_router(graph.router, prefix="/v1")
app.include_router(ingest.router, prefix="/v1")
app.include_router(memory.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")
app.include_router(sec.router, prefix="/v1")
app.include_router(sync.router, prefix="/v1")
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


@functools.lru_cache(maxsize=1)
def expected_migration() -> str:
    """The newest migration in this code's alembic chain."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    root = Path(__file__).resolve().parents[2] / "db"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


@app.get("/ready", tags=["meta"])
async def ready() -> JSONResponse:
    """Ready to serve: the database answers at the migration this code
    expects, and the filing store answers. Unlike /health, anything less is a
    503, which is what a deploy's health check needs: a new version that
    isn't ready never goes live, and the old one keeps serving."""
    from .deps import factory
    log = logging.getLogger("parallx.ready")
    checks = {"database": False, "migration": False, "store": False}
    try:
        async with factory()() as db:
            current = (await db.execute(text("select version_num from alembic_version"))).scalar()
        checks["database"] = True
        checks["migration"] = current == expected_migration()
        if not checks["migration"]:
            log.warning("database is at migration %s, code expects %s",
                        current, expected_migration())
    except Exception as exc:
        log.warning("database not ready: %s", str(exc).splitlines()[0] if str(exc) else exc)
    try:
        await run_in_threadpool(store.check)
        checks["store"] = True
    except Exception as exc:
        log.warning("filing store not ready: %s", exc)
    ok = all(checks.values())
    return JSONResponse({"status": "ready" if ok else "not ready", "version": API_VERSION,
                         **checks}, status_code=200 if ok else 503)


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


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body(),
                        headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None)


@app.exception_handler(SearchUnavailable)
async def _search_unavailable(request: Request, exc: SearchUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": exc.error, "message": exc.message})


@app.exception_handler(UnknownOrganization)
async def _unknown_org(request: Request, exc: UnknownOrganization) -> JSONResponse:
    return JSONResponse(status_code=403, content={"error": "unknown_organization"})
