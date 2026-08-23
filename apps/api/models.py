"""Response models.

`Evidence` is not optional on anything that makes a claim. A card revision, a
promise resolution and a search hit all carry it, so a client cannot render an
uncited assertion even by accident.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    document_id: int
    accession: str
    form_type: str
    page_number: int | None = None
    paragraph_id: str | None = None
    quote: str
    section_path: list[str] = Field(default_factory=list)


class CardFact(BaseModel):
    key: str
    label: str
    value: str | None = None
    unit: str | None = None
    period: str | None = None
    status: str | None = None


class CardDelta(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[dict] = Field(default_factory=list)


class CardRevision(BaseModel):
    revision: int
    as_of: date
    summary: str
    source_note: str | None = None
    is_material: bool
    facts: list[CardFact]
    delta: CardDelta
    evidence: list[Evidence]


class MemoryCard(BaseModel):
    kind: str
    title: str
    source_label: str = Field(description="the 'Updates from' binding, e.g. '10-Q / 10-K'")
    revision_count: int
    material_count: int
    last_updated_at: date | None = None
    current: CardRevision | None = None


class CardDetail(MemoryCard):
    history: list[CardRevision]


class TimelineEntry(BaseModel):
    occurred_at: date
    kind: str
    headline: str
    ref: str
    topic_slug: str | None = None
    evidence: Evidence | None = None


class Promise(BaseModel):
    statement: str
    made_at: date
    horizon: str | None = None
    due_date: date | None = None
    status: Literal["open", "kept", "broken", "abandoned", "unclear"]
    resolved_at: date | None = None
    resolution_note: str | None = None
    made_evidence: Evidence
    resolved_evidence: Evidence | None = None


class MemorySummary(BaseModel):
    company_id: str
    ticker: str | None
    document_count: int
    counts: dict[str, int]
    built_at: date | None = None


class GraphNode(BaseModel):
    id: str
    kind: str
    label: str
    weight: int = 1
    firstSeen: date | None = None
    lastSeen: date | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: str
    weight: int = 1
    documents: list[str] = Field(default_factory=list)


class TopicGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class SearchHit(BaseModel):
    chunk_id: str
    score: float
    text: str
    citation: str
    evidence: Evidence


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    took_ms: int
