from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from ..models import TopicGraph

router = APIRouter(prefix="/companies", tags=["graph"])


@router.get("/{ticker}/graph", response_model=TopicGraph)
async def topic_graph(
    ticker: str,
    min_co_occurrence: int = Query(1, ge=1,
        description="edges need this many shared documents; raise it to cut noise"),
    until: date | None = Query(None,
        description="the graph as it stood on this date — what replay scrubs"),
) -> TopicGraph:
    return TopicGraph(nodes=[], edges=[])
