from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import get_pool
from ..models import MemorySummary

router = APIRouter(prefix="/companies", tags=["companies"])

_SUMMARY = """
select m.company_id, m.ticker, m.document_count, m.built_at,
       (select count(*) from topics   t where t.company_id = m.company_id),
       (select count(*) from people   p where p.company_id = m.company_id),
       (select count(*) from metrics  x where x.company_id = m.company_id),
       (select count(*) from risks    r where r.company_id = m.company_id),
       (select count(*) from promises q where q.company_id = m.company_id),
       (select count(*) from products d where d.company_id = m.company_id),
       (select count(*) from events   e where e.company_id = m.company_id)
  from company_memory m
 where m.ticker = %s
"""


@router.get("/{ticker}", response_model=MemorySummary)
async def get_company(ticker: str) -> MemorySummary:
    async with get_pool().connection() as conn, conn.cursor() as cur:
        await cur.execute(_SUMMARY, (ticker.upper(),))
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"No memory built for {ticker.upper()}")
    return MemorySummary(
        company_id=str(row[0]), ticker=row[1], document_count=row[2], built_at=row[3],
        counts=dict(zip(("topics", "people", "metrics", "risks",
                         "promises", "products", "events"), row[4:])),
    )
