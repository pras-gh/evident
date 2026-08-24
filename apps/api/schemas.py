"""Response schemas.

Anything that makes a claim carries its citation, and the fields are required
rather than optional — a response that *can* omit provenance will eventually
omit it, and an uncited claim is the one failure this product cannot afford.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class TopicOut(BaseModel):
    id: int
    slug: str
    label: str
    first_seen_at: date | None = None
    last_seen_at: date | None = None
    mention_count: int


class MentionOut(BaseModel):
    observed_at: date
    quote: str
    accession: str
    form_type: str
    page_number: int | None = None
    section_title: str | None = None
    paragraph_id: str


class TopicDetailOut(TopicOut):
    mentions: list[MentionOut]


class TimelineEventOut(BaseModel):
    kind: str
    headline: str
    detail: str | None = None
    occurred_at: date
    ref: str
    topic_id: int | None = None


class RiskOut(BaseModel):
    slug: str
    label: str
    category: str | None = None
    severity: str | None = None
    status: str = Field(description="'dropped' means it stopped being disclosed")
    first_seen_at: date | None = None
    last_seen_at: date | None = None


class CompanyMemoryOut(BaseModel):
    company_id: int
    cik: str
    ticker: str | None
    name: str
    document_count: int
    earliest_filing: date | None = None
    latest_filing: date | None = None
    counts: dict[str, int]
    top_topics: list[TopicOut]


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    ticker: str | None = None
    form_types: list[str] | None = None
    k: int = Field(20, ge=1, le=100)


class SearchHitOut(BaseModel):
    chunk_id: int
    paragraph_id: str
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
