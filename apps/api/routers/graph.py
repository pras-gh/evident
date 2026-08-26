"""Memory Graph API.

    GET /v1/company/{ticker}/graph

**This contract is frozen.** Node ids are entity keys rather than database ids
precisely so a client can cache the graph and hold those ids across rebuilds —
a surrogate key would change and break every stored reference silently.

Additive fields may appear. `id`, `label`, `type`, `importance`, `mentions`,
`source`, `target`, `relationship` and `strength` will not change meaning.
`tests/test_graph_contract.py` asserts the shape.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import Company, Document, Entity, EntityMention, Relationship
from evident_graph.builder import EntityInput, build_graph
from evident_graph.relationships import TypedEdge

from ..deps import get_company, get_db
from ..schemas import GraphOut, ImportanceExplanation

router = APIRouter(prefix="/company", tags=["graph"])


@router.get("/{ticker}/graph", response_model=GraphOut, summary="Memory graph")
async def memory_graph(
    company: Company = Depends(get_company),
    db: AsyncSession = Depends(get_db),
    min_importance: int = Query(0, ge=0, le=100),
    limit: int | None = Query(None, ge=1, le=500),
    until: date | None = Query(None, description="the graph as it stood on this date"),
) -> GraphOut:
    rows = (await db.execute(
        select(Entity,
               func.count(EntityMention.id).label("mentions"),
               func.array_agg(func.distinct(EntityMention.document_id))
                   .label("documents"))
        .outerjoin(EntityMention, EntityMention.entity_id == Entity.id)
        .where(Entity.company_id == company.id)
        .group_by(Entity.id)
    )).all()

    entities = [
        EntityInput(key=e.key, label=e.label, kind=e.kind,
                    documents={str(d) for d in (docs or []) if d is not None},
                    mentions=int(mentions or 0),
                    first_seen_at=e.first_seen_at, last_seen_at=e.last_seen_at)
        for e, mentions, docs in rows
        if until is None or (e.first_seen_at is None or e.first_seen_at <= until)
    ]

    by_id = {e.id: e.key for e, _, _ in rows}
    typed_edges = [
        TypedEdge(source_key=by_id[r.source_entity_id],
                  target_key=by_id[r.target_entity_id],
                  relationship=r.kind, document_id=str(d),
                  observed_at=r.last_seen_at)
        for r in (await db.execute(
            select(Relationship).where(Relationship.company_id == company.id)
        )).scalars()
        if r.source_entity_id in by_id and r.target_entity_id in by_id
        for d in (r.document_ids or [0])
    ]

    totals = (await db.execute(
        select(func.count(), func.max(Document.filed_at))
        .where(Document.company_id == company.id))).one()

    graph = build_graph(company=company.ticker or company.cik, entities=entities,
                        typed_edges=typed_edges, total_documents=totals[0] or 1,
                        newest_filing=until or totals[1],
                        min_importance=min_importance, limit=limit)
    return GraphOut(**graph.to_contract())


@router.get("/{ticker}/graph/nodes/{node_id}", response_model=ImportanceExplanation,
            summary="Why a node scored what it did")
async def explain_node(node_id: str, company: Company = Depends(get_company),
                       db: AsyncSession = Depends(get_db)) -> ImportanceExplanation:
    """The contract returns a bare number; this is how a reader gets behind it.

    A score nobody can interrogate is the same failure as an uncited claim.
    """
    from evident_graph.builder import explain

    rows = (await db.execute(
        select(Entity, func.count(EntityMention.id),
               func.array_agg(func.distinct(EntityMention.document_id)))
        .outerjoin(EntityMention, EntityMention.entity_id == Entity.id)
        .where(Entity.company_id == company.id).group_by(Entity.id))).all()
    entities = [EntityInput(key=e.key, label=e.label, kind=e.kind,
                            documents={str(d) for d in (docs or []) if d is not None},
                            mentions=int(m or 0), first_seen_at=e.first_seen_at,
                            last_seen_at=e.last_seen_at)
                for e, m, docs in rows]
    if node_id not in {e.key for e in entities}:
        raise HTTPException(404, f"No node '{node_id}' for {company.ticker}")

    totals = (await db.execute(
        select(func.count(), func.max(Document.filed_at))
        .where(Document.company_id == company.id))).one()
    return ImportanceExplanation(
        **explain(entities, node_id, total_documents=totals[0] or 1,
                  newest_filing=totals[1]))
