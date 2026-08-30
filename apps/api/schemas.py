"""Response schemas.

Anything that makes a claim carries its citation, and the fields are required
rather than optional — a response that *can* omit provenance will eventually
omit it, and an uncited claim is the one failure this product cannot afford.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class EntityOut(BaseModel):
    id: int
    entity_type: str
    slug: str
    name: str
    description: str | None = None
    attributes: dict = Field(default_factory=dict)
    status: str
    first_seen: date | None = None
    latest_seen: date | None = None
    mention_count: int
    importance_score: float = 0.0


class Provenance(BaseModel):
    """Where an extracted claim came from. Present on every extracted object."""
    chunk_hash: str | None = None
    document_id: int
    page: int | None = None
    paragraph_id: str | None = None
    confidence: float | None = Field(
        None, ge=0, le=1,
        description=("the extractor's own reported score — a self-report, not a "
                     "calibrated probability. Use it for ranking and triage, "
                     "not as a likelihood that the claim is true."))


class MentionOut(BaseModel):
    observed_at: date
    quote: str
    accession: str
    form_type: str
    section_title: str | None = None
    provenance: Provenance


class EntityDetailOut(EntityOut):
    mentions: list[MentionOut]


class TimelineEventOut(BaseModel):
    kind: str
    headline: str
    detail: str | None = None
    occurred_at: date
    ref: str
    entity_id: int | None = None


class CompanyMemoryOut(BaseModel):
    company_id: int
    cik: str
    ticker: str | None
    name: str
    document_count: int
    earliest_filing: date | None = None
    latest_filing: date | None = None
    counts: dict[str, int]
    top_entities: list[EntityOut]


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    ticker: str | None = None
    form_types: list[str] | None = None
    k: int = Field(20, ge=1, le=100)


class SearchHitOut(BaseModel):
    chunk_id: int
    chunk_hash: str
    paragraph_ids: list[str] = Field(
        description="the source paragraphs this chunk was built from")
    score: float
    text: str
    accession: str
    form_type: str
    filed_at: date
    page_number: int | None = None
    section_title: str | None = None
    citation: str


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitOut]
    took_ms: float
    embedder: str


# ---------------------------------------------------------------- graph
# Frozen contract. Field names and meanings do not change; new fields may be
# added. See apps/api/routers/graph.py and tests/test_graph_contract.py.
class GraphNode(BaseModel):
    id: str = Field(description="entity key — stable across rebuilds, safe to cache")
    label: str
    type: str = Field(description="topic | strategy | person | product | metric "
                                  "| risk | event | segment")
    importance: int = Field(ge=0, le=100)
    mentions: int = Field(ge=0)


class GraphEdge(BaseModel):
    source: str
    target: str
    relationship: str
    strength: float = Field(ge=0, le=1)


class GraphOut(BaseModel):
    company: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ImportanceExplanation(BaseModel):
    """Behind the bare number in the contract."""
    id: str
    importance: int
    components: dict[str, float]
    signals: dict
