"""On-demand fetches of a company's filings from EDGAR.

    GET /v1/fetches/{id}

A search naming a company with no filings here calls `request_fetch`, which
starts `workers/fetch_worker.py` in its own process — parsing a 10-K is
seconds of CPU the API should not spend — or joins the fetch already under
way. The search page polls this endpoint, not the search, so waiting costs no
model calls.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import FETCH_ACTIVE, CompanyFetch

from ..deps import get_db
from ..schemas import FetchOut

router = APIRouter(tags=["fetches"])
log = logging.getLogger("evident.fetches")

ROOT = Path(__file__).resolve().parents[3]
#: A fetch running longer than this has died with its process (an API reload
#: does that); the next search starts another.
STALE_AFTER = timedelta(minutes=20)
#: A failed fetch is not retried sooner than this, so a company EDGAR cannot
#: serve is not asked for on every keystroke.
RETRY_AFTER = timedelta(minutes=10)


def launch(fetch_id: int) -> None:
    """Start the worker in its own process, logging to data/fetch-logs/. It
    outlives an API reload. Replaced in tests."""
    logs = ROOT / "data" / "fetch-logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # the worker imports what the API imports, however the API was started
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    with open(logs / f"{fetch_id}.log", "ab") as out:
        subprocess.Popen([sys.executable, "-m", "workers.fetch_worker", str(fetch_id)],
                         cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True)


async def request_fetch(db: AsyncSession, *, cik: str, ticker: str, name: str) -> CompanyFetch:
    """The fetch under way for this company, the recent failure, or a new
    fetch, started. Committed before it starts: the worker reads the row."""
    now = datetime.now(timezone.utc)
    latest = (await db.execute(
        select(CompanyFetch).where(CompanyFetch.cik == cik)
        .order_by(CompanyFetch.requested_at.desc()).limit(1))).scalar_one_or_none()
    if latest is not None:
        if latest.status in FETCH_ACTIVE:
            if now - (latest.started_at or latest.requested_at) < STALE_AFTER:
                return latest
            latest.status, latest.error, latest.finished_at = (
                "failed", "stopped without finishing", now)
            await db.flush()
        elif (latest.status == "failed"
              and now - (latest.finished_at or latest.requested_at) < RETRY_AFTER):
            return latest

    # Two searches at once: the partial unique index lets one insert win, and
    # the other joins the fetch it started.
    new_id = (await db.execute(
        insert(CompanyFetch).values(cik=cik, ticker=ticker, name=name, status="queued")
        .on_conflict_do_nothing(index_elements=["cik"],
                                index_where=text("status in ('queued','running')"))
        .returning(CompanyFetch.id))).scalar_one_or_none()
    await db.commit()
    if new_id is None:
        return (await db.execute(select(CompanyFetch).where(
            CompanyFetch.cik == cik, CompanyFetch.status.in_(FETCH_ACTIVE)))).scalar_one()
    log.info("fetching %s (%s, CIK %s): fetch %s", name, ticker, cik, new_id)
    launch(new_id)
    return await db.get(CompanyFetch, new_id)


@router.get("/fetches/{fetch_id}", response_model=FetchOut, summary="An on-demand fetch")
async def get_fetch(fetch_id: int, db: AsyncSession = Depends(get_db)) -> FetchOut:
    row = await db.get(CompanyFetch, fetch_id)
    if row is None:
        raise HTTPException(404, f"no fetch {fetch_id}")
    return FetchOut.model_validate(row, from_attributes=True)
