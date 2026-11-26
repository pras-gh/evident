"""Response schemas.

Anything that makes a claim carries its citation, and the fields are required
rather than optional — a response that *can* omit provenance will eventually
omit it, and an uncited claim is the one failure this product cannot afford.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
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


class SearchCompanyOut(BaseModel):
    name: str
    ticker: str | None = None
    #: whether Parallx holds filings for it
    covered: bool
    #: for a company not covered: its fetch from EDGAR — queued, running or
    #: failed; "not_on_edgar" when the SEC has no such filer; "not_configured"
    #: when EDGAR cannot be asked (no SEC_USER_AGENT); None if not attempted
    fetch: Literal["queued", "running", "failed", "not_on_edgar", "not_configured"] | None = None
    fetch_id: int | None = None
    fetch_error: str | None = None


class AskRequest(BaseModel):
    """A question, with what search already worked out about it — so the
    question is not read twice, nor the passages searched for again."""
    query: str = Field(min_length=2, max_length=1000)
    #: from the search response: "market" and "general" are answered from the
    #: web, "filings" from the passages
    kind: Literal["market", "filings", "general"] | None = None
    #: the passages search found, best first; the filings answer may cite them
    chunk_ids: list[int] = Field(default_factory=list, max_length=20)


class AskSourceOut(BaseModel):
    title: str
    uri: str


class AskCitationOut(BaseModel):
    #: the number the answer cites it by, as in "[2]"
    n: int
    chunk_id: int
    paragraph_id: str | None
    company: str
    form_type: str
    filed_at: date
    citation: str


class AskResponse(BaseModel):
    #: where the answer came from; "none" when there is no answer, and
    #: `notice` says why
    mode: Literal["web", "filings", "none"]
    answer: str | None = None
    #: web answers: the pages it drew on
    sources: list[AskSourceOut] = Field(default_factory=list)
    #: web answers: Google's Search Suggestions as HTML, which its terms
    #: require shown with the answer
    search_suggestions: str | None = None
    #: filings answers: the passages it cites
    citations: list[AskCitationOut] = Field(default_factory=list)
    #: what the reader should know: live answers not enabled, a fallback taken
    notice: str | None = None
    model: str | None = None
    cost_usd: Decimal | None = None


class FetchOut(BaseModel):
    id: int
    cik: str
    ticker: str | None
    name: str
    status: Literal["queued", "running", "done", "failed"]
    error: str | None = None
    accession: str | None = None
    form_type: str | None = None
    chunks: int | None = None
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SearchScopeOut(BaseModel):
    #: the companies searched; None for every company on record, [] for none
    tickers: list[str] | None
    #: why: the query named them, the request's `ticker` filter, or neither
    reason: Literal["named", "filter", "all"]


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitOut]
    took_ms: float
    #: provider/model that embedded the query; None when nothing was searched
    embedder: str | None = None
    #: companies the query names, whether or not Parallx covers them
    companies: list[SearchCompanyOut] = Field(default_factory=list)
    scope: SearchScopeOut | None = None
    #: what the query asks with the companies taken out; searched instead of
    #: the whole query when there is one
    topic: str | None = None
    #: every company Parallx holds filings for
    on_record: list[SearchCompanyOut] = Field(default_factory=list)
    #: "rules", or "rules+provider:model" when a model also read the query
    understood_by: str = "rules"
    #: what answering the query needs — "market" (live information), "filings"
    #: or "general"; None when no model read the query
    kind: Literal["market", "filings", "general"] | None = None
    #: the query asked to see filings or documents
    wants_filings: bool = False


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
    """Where a paragraph sits on its page.

    Points (1/72 in) from the **top-left** of the page as a viewer shows it
    (the PDF's CropBox), y down — the convention of pdf.js and of screens. To
    draw it over a page rendered `w` pixels wide, scale every coordinate by
    `w / page_width`. `y0` is the top edge, `y1` the bottom.

    Present for PDF filings, measured from the text's position and the font's
    metrics. **Null for HTML filings**, which have no page geometry until a
    browser lays them out; for those the paragraph `anchor` is the exact
    target, and a box invented server-side would look precise and be wrong.
    """
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float
    page_height: float


class HighlightOut(BaseModel):
    """One thing to light up for a citation: a paragraph, or a whole table."""
    anchor: str
    paragraph_id: str | None = None
    #: this paragraph's page — a chunk-wide citation can span pages
    page: int | None = None
    bounding_box: BoundingBox | None = None
    #: `#rrggbb` to draw it in — the cited entity's colour, or the default
    highlight_color: str = "#34d399"


class EvidenceEntityOut(BaseModel):
    """An entity extraction found in the cited paragraph."""
    slug: str
    name: str
    entity_type: str
    confidence: float | None = None
    highlight_color: str


class EvidenceOut(BaseModel):
    """One resolved citation: where it is, what it says, how sure we are."""
    chunk_id: int
    document_id: int
    ticker: str | None = None
    company: str | None = None
    accession: str
    form_type: str
    fiscal_period: str | None = None
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
    #: the cited paragraph's box (the first paragraph's, for a chunk-wide
    #: citation); null for HTML filings and tables — see BoundingBox
    bounding_box: BoundingBox | None = None
    #: every anchor to highlight, in document order, each with its own page
    #: and box — what a viewer needs to mark the citation exactly
    highlights: list[HighlightOut] = Field(default_factory=list)
    #: `#rrggbb` for this citation — the named entity's colour, or the default
    highlight_color: str = "#34d399"
    #: entities extracted from the cited paragraph, most confident first
    entities: list[EvidenceEntityOut] = Field(default_factory=list)
    citation: str


#: The most citations one POST /v1/evidence/resolve accepts.
MAX_RESOLVE = 1000


class CitationIn(BaseModel):
    chunk_id: int
    paragraph_id: str | None = None
    #: scope `confidence` to one entity's mentions
    entity: str | None = None


class ResolveIn(BaseModel):
    citations: list[CitationIn] = Field(min_length=1, max_length=MAX_RESOLVE)


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
    #: PDFs only — see BoundingBox
    bounding_box: BoundingBox | None = None


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
    #: share of a quote's four-word phrases that must be in the other filing
    #: for the quote to count as said there too
    carried_over: float


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
    #: new, returning or dropped topics that extraction implied but the text
    #: did not bear out (a sentence quoted for the topic is in both filings),
    #: matching the same filters; not in `events`
    withheld: int = 0
    events: list[TimelineEntryOut]


# --------------------------------------------------------------------------
# Companies and memory cards


class CompanyListItem(BaseModel):
    ticker: str | None
    name: str
    cik: str
    document_count: int
    latest_filing: date | None = None
    #: its GICS sector, for an S&P 500 company
    sector: str | None = None
    #: the name as people write it ("Nvidia"), where the S&P 500 list has
    #: one; else EDGAR's ("NVIDIA CORP")
    display_name: str
    #: whether search can find its filings yet (some chunks embedded)
    searchable: bool = False


class SyncRunOut(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    seen: int
    new: int
    fetched: int
    failed: int
    error: str | None = None


class SyncFilingOut(BaseModel):
    ticker: str | None
    name: str
    form_type: str | None
    accession: str | None
    accepted_at: datetime | None
    fetched_at: datetime | None
    minutes_after_filing: float | None


class SyncStatusOut(BaseModel):
    """The S&P 500 as Parallx holds it."""
    tracked: int
    with_annual_report: int
    #: every filing on record embedded
    searchable: int
    #: chunks parsed and waiting for a vector, all companies
    pending_chunks: int
    backfill_failures_last_day: int
    last_poll: SyncRunOut | None = None
    last_sweep: SyncRunOut | None = None
    latest_filings: list[SyncFilingOut] = []


class CompanySummaryOut(BaseModel):
    company_id: int
    cik: str
    ticker: str | None
    name: str
    document_count: int
    earliest_filing: date | None = None
    latest_filing: date | None = None
    #: entities per type
    counts: dict[str, int]
    #: when extraction last wrote to this company's memory
    built_at: datetime | None = None


class CardFactOut(BaseModel):
    key: str
    label: str
    value: str | None = None
    unit: str | None = None
    period: str | None = None
    status: str | None = None


class CardChangeOut(BaseModel):
    label: str
    before: str | None = None
    after: str | None = None


class CardDeltaOut(BaseModel):
    added: list[str]
    removed: list[str]
    changed: list[CardChangeOut]


class CardEvidenceOut(BaseModel):
    """Where a card fact comes from. Open it with the evidence viewer via
    chunk_id and paragraph_id."""
    document_id: int
    accession: str | None
    form_type: str | None
    page_number: int | None
    paragraph_id: str | None
    quote: str
    section_path: list[str]
    chunk_id: int | None = None
    #: the entity the fact is about: /evidence/{ticker}/{entity_slug}?cite=…
    entity_slug: str | None = None


class CardRevisionOut(BaseModel):
    revision: int
    as_of: date
    summary: str
    source_note: str | None = None
    #: false when a filing touched the card without moving anything
    is_material: bool
    facts: list[CardFactOut]
    delta: CardDeltaOut
    evidence: list[CardEvidenceOut]


class MemoryCardOut(BaseModel):
    kind: str
    title: str
    #: the routing binding — "Updates from"
    source_label: str
    revision_count: int
    material_count: int
    last_updated_at: date | None = None
    current: CardRevisionOut | None = None
    #: why the card has no history, when it cannot have one yet
    unavailable: str | None = None


class CardDetailOut(MemoryCardOut):
    #: oldest first
    history: list[CardRevisionOut]


# --------------------------------------------------------------------------
# Documents and pages: what the evidence viewer navigates


class DocumentSummaryOut(BaseModel):
    document_id: int
    accession: str
    form_type: str
    fiscal_period: str | None = None
    filed_at: date
    #: when the filing became public (EDGAR acceptance time)
    published_at: datetime
    #: "html" or "pdf"
    source_format: str
    page_count: int | None = None
    #: pages that have stored text; the rest of a filing may not be ingested
    pages_with_text: int
    first_page: int | None = None
    paragraph_count: int
    #: true when paragraphs carry bounding boxes (PDF filings)
    has_bounding_boxes: bool
    #: the filing's index page on EDGAR
    url: str


class CompanyDocumentsOut(BaseModel):
    ticker: str | None
    company: str
    #: newest first
    documents: list[DocumentSummaryOut]


class PageOut(BaseModel):
    """One page of a filing: its paragraphs, anchors and boxes."""
    document_id: int
    accession: str
    form_type: str
    filed_at: date
    ticker: str | None
    source_format: str
    page: int
    page_count: int | None = None
    #: the page's size in points, when the filing has geometry (PDFs)
    page_width: float | None = None
    page_height: float | None = None
    #: the nearest pages before and after this one that have text
    prev_page: int | None = None
    next_page: int | None = None
    #: the page as rendered, when it has been (see DocumentDetailOut)
    image_url: str | None = None
    thumbnail_url: str | None = None
    blocks: list[DocumentBlockOut]


class PageSummaryOut(BaseModel):
    page: int
    #: points; boxes on this page are in the same space
    width: float
    height: float
    #: API paths, versioned by render time so they can be cached forever
    image_url: str | None = None
    thumbnail_url: str | None = None
    paragraph_count: int


class DocumentDetailOut(BaseModel):
    """A filing and its pages, as the viewer shows them."""
    document_id: int
    ticker: str | None
    company: str
    accession: str
    form_type: str
    fiscal_period: str | None = None
    filed_at: date
    source_format: str
    page_count: int | None = None
    #: null until the render worker has run; the viewer then shows text
    rendered_at: datetime | None = None
    #: a PDF of the filing whose page N is page N
    pdf_url: str | None = None
    #: the filing's index page on EDGAR
    url: str
    #: every page, rendered or not, in order
    pages: list[PageSummaryOut]


# --------------------------------------------------------------------------
# Identity and AI usage


class OrganizationOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    is_internal: bool


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str
    organization: OrganizationOut


class UsageTotals(BaseModel):
    cost_usd: Decimal
    #: distinct requests, whatever happened to them
    requests: int
    #: provider calls, retries and fallbacks included
    attempts: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    #: total cost over requests that were answered (by a provider or the cache)
    avg_cost_per_request: Decimal
    #: requests no provider answered
    failed_requests: int
    failed_attempts: int
    #: requests answered by a fallback provider
    fallback_requests: int
    #: fallback requests / requests a provider answered
    fallback_rate: float
    cache_hits: int
    blocked_requests: int


class UsageBreakdownRow(BaseModel):
    key: str
    label: str
    cost_usd: Decimal
    requests: int
    input_tokens: int
    output_tokens: int
    failed_attempts: int


class UsagePoint(BaseModel):
    period: str
    cost_usd: Decimal
    requests: int


class UsageEventOut(BaseModel):
    created_at: datetime
    request_id: uuid.UUID
    organization: str
    provider: str
    model: str
    task_type: str
    status: str
    error_message: str | None = None


class UsageFacets(BaseModel):
    providers: list[str]
    models: list[str]
    #: only for platform admins
    organizations: list[OrganizationOut]


class UsageSummaryOut(BaseModel):
    #: what the caller is allowed to see, after applying their role
    organization_id: uuid.UUID | None
    user_id: uuid.UUID | None
    date_from: date
    date_to: date
    totals: UsageTotals
    by_provider: list[UsageBreakdownRow]
    by_model: list[UsageBreakdownRow]
    by_organization: list[UsageBreakdownRow]
    by_task: list[UsageBreakdownRow]
    daily: list[UsagePoint]
    monthly: list[UsagePoint]
    recent_failures: list[UsageEventOut]
    facets: UsageFacets


class LimitStatus(BaseModel):
    limit: str
    allowed: Decimal
    used: Decimal
    #: when the window resets; null for per-request limits
    resets_at: datetime | None = None


class UsageLimitsOut(BaseModel):
    organization_id: uuid.UUID
    limits: list[LimitStatus]


class SummaryPointOut(BaseModel):
    point: str
    #: each cited paragraph, with what the evidence viewer needs to open it
    citations: list[dict]


class FilingSummaryOut(BaseModel):
    document_id: int
    accession: str
    summary: str
    key_points: list[SummaryPointOut]
    request_id: uuid.UUID
    provider: str
    model: str
    cached: bool
    fallback: bool
    cost_usd: Decimal
