from __future__ import annotations

from fastapi import APIRouter, Query

from ..deps import get_pool
from ..models import Promise, TimelineEntry

router = APIRouter(prefix="/companies", tags=["memory"])


@router.get("/{ticker}/timeline", response_model=list[TimelineEntry])
async def timeline(ticker: str, limit: int = Query(50, le=500),
                   kind: str | None = None) -> list[TimelineEntry]:
    async with get_pool().connection() as conn, conn.cursor() as cur:
        await cur.execute("""
            select t.occurred_at, t.kind, t.headline, t.ref_table || ':' || t.ref_id
              from timeline t
              join company_memory m on m.company_id = t.company_id
             where m.ticker = %s and (%s is null or t.kind = %s)
             order by t.occurred_at desc
             limit %s
        """, (ticker.upper(), kind, kind, limit))
        rows = await cur.fetchall()
    return [TimelineEntry(occurred_at=r[0], kind=r[1], headline=r[2], ref=r[3])
            for r in rows]


@router.get("/{ticker}/promises", response_model=list[Promise])
async def promises(ticker: str,
                   status: str | None = Query(None,
                       description="open | kept | broken | abandoned | unclear")
                   ) -> list[Promise]:
    """Forward-looking commitments.

    `unclear` means the horizon passed and nothing in a later filing settled it.
    It is deliberately distinct from `broken`: silence is not evidence of
    failure, and an unresolved commitment is itself the finding.
    """
    return []
