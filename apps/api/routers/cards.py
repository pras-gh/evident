"""Memory cards.

The list endpoint returns each card's *current* revision. The detail endpoint
returns the whole history, because that is the part a stat tile cannot show and
the reason a card exists.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..deps import get_pool
from ..models import CardDetail, MemoryCard

router = APIRouter(prefix="/companies", tags=["cards"])


@router.get("/{ticker}/cards", response_model=list[MemoryCard])
async def list_cards(ticker: str) -> list[MemoryCard]:
    async with get_pool().connection() as conn, conn.cursor() as cur:
        await cur.execute("""
            select c.kind, d.title, s.source_label, c.revision_count,
                   (select count(*) from card_revisions r
                     where r.card_id = c.id and r.is_material),
                   c.last_updated_at
              from memory_cards c
              join company_memory m  on m.company_id = c.company_id
              join card_definitions d on d.kind = c.kind
              join lateral (select source_label from card_sources
                             where card_kind = c.kind
                             order by priority desc limit 1) s on true
             where m.ticker = %s
             order by d.display_order
        """, (ticker.upper(),))
        rows = await cur.fetchall()
    return [MemoryCard(kind=r[0], title=r[1], source_label=r[2],
                       revision_count=r[3], material_count=r[4],
                       last_updated_at=r[5]) for r in rows]


@router.get("/{ticker}/cards/{kind}", response_model=CardDetail)
async def get_card(ticker: str, kind: str,
                   materially: bool = Query(False,
                       description="return only revisions where something moved")
                   ) -> CardDetail:
    raise HTTPException(501, "Not implemented — see docs/api.md")
