"""Evident API.

Read-only over the three layers. Every response that carries a claim also
carries the evidence for it — that is enforced at the response-model level
rather than left to each endpoint's discretion, because an endpoint that
forgets is indistinguishable from one that had nothing to cite.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import cards, companies, graph, memory, search

API_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .deps import close_pool, open_pool
    await open_pool(os.environ["DATABASE_URL"])
    yield
    await close_pool()


app = FastAPI(
    title="Evident API",
    version=API_VERSION,
    summary="Structured company intelligence from SEC filings.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

for router in (companies.router, memory.router, cards.router,
               graph.router, search.router):
    app.include_router(router, prefix="/v1")


@app.get("/health", tags=["meta"])
async def health() -> dict:
    from .deps import pool_ready
    return {"status": "ok", "version": API_VERSION, "db": await pool_ready()}
