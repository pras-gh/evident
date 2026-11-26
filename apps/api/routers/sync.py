"""How complete and how current Parallx is.

    GET /v1/sync

The S&P 500 (`config/sp500.csv`) is backfilled and watched by
`workers/sync_worker.py`. This reports how many of those companies are on
record and searchable, the last checks of EDGAR, and the newest filings
fetched as they came in, each with how long after EDGAR accepted it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas import SyncStatusOut

router = APIRouter(tags=["sync"])


@router.get("/sync", response_model=SyncStatusOut, summary="S&P 500 coverage and freshness")
async def sync_status(db: AsyncSession = Depends(get_db)) -> SyncStatusOut:
    from parallx_db.sync import status
    from parallx_parser.universe import load
    ciks = [t.cik for t in load()]
    return SyncStatusOut(**await db.run_sync(lambda s: status(s, ciks)))
