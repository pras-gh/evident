"""Response schemas.

Anything that makes a claim carries its citation, and the fields are required
rather than optional — a response that *can* omit provenance will eventually
omit it, and an uncited claim is the one failure this product cannot afford.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

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
    #: what a citation points at; resolve it with GET /v1/evidence/{chunk_id}
    chunk_id: int | None = None
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


# ------------------------------------------------------------------ evidence
class BoundingBox(BaseModel):
    """A rectangle on a page, in PDF points from the top-left.

    Always null today. HTML filings have no fixed layout to take coordinates
    from, and the PDF parser does not record them. The field is part of the
    contract so a PDF source can populate it later without a breaking change —
    clients should fall back to the paragraph anchor when it is absent, which
    for HTML is the more precise target anyway.
    """
    x0: float
    y0: float
    x1: float
    y1: float


class EvidenceOut(BaseModel):
    """One resolved citation: where it is, what it says, how sure we are."""
    chunk_id: int
    document_id: int
    accession: str
    form_type: str
    filed_at: date
    source_format: str
    #: the page the *cited paragraph* is on — not the chunk's first page,
    #: which is wrong for any paragraph after a page break
    page: int | None
    #: the paragraph this citation points at; null for a table chunk
    paragraph_id: str | None
    #: every paragraph in the chunk, in order
    paragraph_ids: list[str]
    #: DOM anchors the viewer highlights, in document order
    anchors: list[str]
    section_title: str | None = None
    #: the cited paragraph's text, or the whole chunk for a table
    text: str
    confidence: float | None = Field(
        None, ge=0, le=1,
        description=("highest extraction confidence among mentions citing this "
                     "paragraph (or this entity, if one was named); null when "
                     "nothing has been extracted from it. A self-report, not a "
                     "calibrated probability."))
    bounding_box: BoundingBox | None = None
    citation: str


class CitationIn(BaseModel):
    chunk_id: int
    paragraph_id: str | None = None
    #: scope `confidence` to one entity's mentions
    entity: str | None = None


class ResolveIn(BaseModel):
    citations: list[CitationIn] = Field(min_length=1, max_length=100)


class ResolvedCitation(BaseModel):
    """One citation's outcome. A bad citation is reported, not raised.

    An answer with five citations where one points at a deleted chunk should
    still show the other four; failing the whole request would hide good
    evidence behind one stale reference.
    """
    index: int
    resolved: bool
    evidence: EvidenceOut | None = None
    error: str | None = None


class ResolveOut(BaseModel):
    citations: list[ResolvedCitation]


class DocumentBlockOut(BaseModel):
    anchor: str
    kind: Literal["paragraph", "table"]
    chunk_id: int
    paragraph_id: str | None = None
    section_title: str | None = None
    text: str


class DocumentPageOut(BaseModel):
    #: null collects text whose page was never recorded
    page: int | None
    blocks: list[DocumentBlockOut]


class DocumentPagesOut(BaseModel):
    """The filing as a paged reading view, rebuilt from stored chunks.

    Not a facsimile: the raw filing is not stored, so layout, images and
    styling are gone. Every paragraph is present exactly once, in order, on the
    page it was parsed from, and addressable by its anchor.
    """
    document_id: int
    accession: str
    form_type: str
    filed_at: date
    source_format: str
    ticker: str
    page_count: int | None
    pages: list[DocumentPageOut]


# --------------------------------------------------------------------------
# Timeline: what changed between filings, derived on read


TimelineKind = Literal["newly_disclosed", "disclosed_again", "expanded", "narrowed",
                       "no_longer_disclosed", "filed"]


class TimelineFilingOut(BaseModel):
    document_id: int
    accession: str
    form_type: str
    fiscal_period: str | None = None
    filed_at: date
    page_count: int | None = None
    #: baseline — the earliest filing of its form, compared with nothing, so
    #: nothing in it is reported as new; compared — against the previous
    #: filing of its form; not compared — an amendment or a non-periodic form
    coverage: Literal["baseline", "compared", "not compared"]
    #: the filing's index page on EDGAR
    url: str


class TimelineFilingRef(BaseModel):
    document_id: int
    accession: str
    form_type: str
    fiscal_period: str | None = None
    filed_at: date


class TimelineTopicOut(BaseModel):
    slug: str
    name: str
    entity_type: str


class TimelineEvidenceOut(BaseModel):
    """The paragraph that shows the change. Resolve it with
    GET /v1/evidence/{chunk_id}?paragraph_id=…, or open it in the viewer."""
    chunk_id: int | None
    paragraph_id: str | None
    document_id: int
    page: int | None
    quote: str | None
    confidence: float | None = None
    #: true when no paragraph with this text cited the topic in the filing this
    #: one was compared with; null when there was nothing to compare
    new_paragraph: bool | None = None
    citation: str


class TimelineEntryOut(BaseModel):
    #: stable across requests: accession, kind and entity slug
    id: str
    date: date
    #: "Feb 2025"
    date_label: str
    kind: TimelineKind
    #: the entity's type, title-cased ("Risk"), or "Filing"
    category: str
    title: str
    summary: str
    topic: TimelineTopicOut | None = None
    filing: TimelineFilingRef
    compared_with: TimelineFilingRef | None = None
    #: paragraphs citing the topic in this filing, and in the one compared with
    paragraphs: int | None = None
    previous_paragraphs: int | None = None
    #: null only for `filed` events; every change points at a paragraph
    evidence: TimelineEvidenceOut | None = None


class TimelineThresholds(BaseModel):
    min_delta: int
    min_ratio: float


class CompanyTimelineOut(BaseModel):
    ticker: str
    company: str
    #: every filing considered, newest first, with how it was used
    filings: list[TimelineFilingOut]
    thresholds: TimelineThresholds
    #: events per category among those matching every filter but `category`,
    #: so filter chips can show what each would return
    categories: dict[str, int]
    #: events matching the filters, before `limit`
    total: int
    events: list[TimelineEntryOut]
