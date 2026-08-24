"""Evident Memory Engine API.

Read-only over the memory schema. Every endpoint that returns a claim also
returns the citation behind it.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .routers import ingest, memory, search

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

app.include_router(ingest.router, prefix="/v1")
app.include_router(memory.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")


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
