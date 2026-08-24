"""SQLAlchemy models — the V1 memory engine.

Nine tables. The shape is driven by one requirement: every claim the product
makes must name the paragraph that supports it. So `chunks` carries page number
and section title, and every derived entity references a chunk rather than
floating free.

Two invariants worth stating because they are enforced here rather than in
application code:

  * A topic is unique per company by slug. The memory builder therefore *updates*
    a topic on re-ingest instead of creating a second one — the unique constraint
    makes duplicating a topic a database error rather than a silent bug.
  * Every mention, event, risk observation and metric observation carries the
    chunk it came from, so provenance survives to query time.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime,
                        Float, ForeignKey, Index, Integer, Numeric, String,
                        Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, str16, str64, str255, updated_at

EMBEDDING_DIM = 1536


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cik: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    ticker: Mapped[Optional[str16]] = mapped_column(index=True)
    name: Mapped[str255]
    sic: Mapped[Optional[str16]]
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    documents: Mapped[list["Document"]] = relationship(
        back_populates="company", cascade="all, delete-orphan")
    topics: Mapped[list["Topic"]] = relationship(
        back_populates="company", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_company_id_filed_at", "company_id", "filed_at"),
        CheckConstraint("source_format in ('html','pdf')", name="source_format"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    accession: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    form_type: Mapped[str16] = mapped_column(index=True)
    fiscal_period: Mapped[Optional[str16]]
    filed_at: Mapped[date] = mapped_column(Date)
    # when it hit the wire per EDGAR — never the time we fetched it
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text)
    source_format: Mapped[str16]
    # lets a re-ingest detect unchanged bytes and skip the work
    content_sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[Optional[int]]
    created_at: Mapped[created_at]

    company: Mapped["Company"] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    """A retrievable span, with everything a citation needs.

    Two distinct identities, and conflating them loses the product's core
    promise:

      * `chunk_key` identifies this chunk. Content-addressed, so re-ingesting an
        unchanged filing produces the same key.
      * `paragraph_ids` lists the source paragraphs it was built from. This is
        what lets an answer cite "paragraph 3" rather than gesturing at a span
        of text somebody assembled.

    An oversized paragraph is split into parts keyed `p_abc#1`, `p_abc#2`, so a
    part still resolves to its source paragraph.
    """
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_key",
                         name="uq_chunks_document_id_chunk_key"),
        Index("ix_chunks_document_id_ordinal", "document_id", "ordinal"),
        # the ANN index; ivfflat/hnsw both require a fixed dimension
        Index("ix_chunks_embedding", "embedding",
              postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64},
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_key: Mapped[str64]
    paragraph_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    ordinal: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[Optional[int]]
    section_title: Mapped[Optional[str255]]
    section_path: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    token_estimate: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIM))
    # provenance for the vector itself, so a re-embed or an A/B never has to
    # guess what produced a row
    embedding_provider: Mapped[Optional[str64]]
    embedding_model: Mapped[Optional[str64]]
    embedded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    document: Mapped["Document"] = relationship(back_populates="chunks")

    def citation(self) -> str:
        page = f"p. {self.page_number}" if self.page_number else "—"
        return f"{page}{' · ' + self.section_title if self.section_title else ''}"


class Topic(Base):
    """Unique per company by slug — that is what makes the builder update.

    The constraint is the mechanism, not a safety net. Without it, "update
    existing memory instead of duplicating topics" would be an application-level
    convention that one careless insert breaks silently.
    """
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_topics_company_id_slug"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str255]
    label: Mapped[str255]
    first_seen_at: Mapped[Optional[date]] = mapped_column(Date)
    last_seen_at: Mapped[Optional[date]] = mapped_column(Date)
    mention_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    company: Mapped["Company"] = relationship(back_populates="topics")
    mentions: Mapped[list["TopicMention"]] = relationship(
        back_populates="topic", cascade="all, delete-orphan")


class TopicMention(Base):
    __tablename__ = "topic_mentions"
    __table_args__ = (
        UniqueConstraint("topic_id", "chunk_id",
                         name="uq_topic_mentions_topic_id_chunk_id"),
        Index("ix_topic_mentions_topic_id_observed_at", "topic_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"))
    observed_at: Mapped[date] = mapped_column(Date)
    quote: Mapped[str] = mapped_column(Text)

    topic: Mapped["Topic"] = relationship(back_populates="mentions")


class TimelineEvent(Base):
    """The materialised spine — one indexed read instead of a wide union."""
    __tablename__ = "timeline_events"
    __table_args__ = (
        UniqueConstraint("company_id", "kind", "ref", "occurred_at",
                         name="uq_timeline_events_company_id_kind_ref_occurred_at"),
        Index("ix_timeline_events_company_id_occurred_at",
              "company_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"))
    topic_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str64] = mapped_column(index=True)
    headline: Mapped[str] = mapped_column(Text)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    occurred_at: Mapped[date] = mapped_column(Date)
    ref: Mapped[str255]
    created_at: Mapped[created_at]


class Risk(Base):
    """A risk that stops being disclosed is marked, not deleted.

    The disappearance of a risk factor between two 10-Ks is one of the more
    informative things in the corpus, so `status` changes and the history stays
    queryable.
    """
    __tablename__ = "risks"
    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_risks_company_id_slug"),
        CheckConstraint("status in ('active','dropped')", name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"))
    slug: Mapped[str255]
    label: Mapped[str] = mapped_column(Text)
    category: Mapped[Optional[str64]]
    severity: Mapped[Optional[str64]]
    status: Mapped[str16] = mapped_column(default="active", server_default="active")
    first_seen_at: Mapped[Optional[date]] = mapped_column(Date)
    last_seen_at: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


class Person(Base):
    """`normalised` is the identity; `full_name` is the display.

    'Mr. Jensen Huang', 'JENSEN HUANG' and 'Huang, Jensen' are one person, and
    the unique constraint on the normalised form is what makes that true at the
    database level rather than by convention.
    """
    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint("company_id", "normalised",
                         name="uq_people_company_id_normalised"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"))
    full_name: Mapped[str255]
    normalised: Mapped[str255] = mapped_column(index=True)
    # dated because executives change jobs, and "the CFO said" means a
    # different person depending on the year
    roles: Mapped[Optional[dict]] = mapped_column(JSONB)
    first_seen_at: Mapped[Optional[date]] = mapped_column(Date)
    last_seen_at: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


class Metric(Base):
    """Not in the V1 table list, but deliverable 4 extracts metrics and they
    need a home. Normalised so label drift (`CapEx` / `Capital Expenditures`)
    stays one series."""
    __tablename__ = "metrics"
    __table_args__ = (
        UniqueConstraint("company_id", "normalised",
                         name="uq_metrics_company_id_normalised"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str255]
    normalised: Mapped[str255]
    unit: Mapped[Optional[str64]]
    created_at: Mapped[created_at]

    observations: Mapped[list["MetricObservation"]] = relationship(
        back_populates="metric", cascade="all, delete-orphan")


class MetricObservation(Base):
    """One row per (metric, period, document).

    Including `document_id` in the key is deliberate: a second observation of
    the same period from a *later* filing is a restatement, not a duplicate.
    Companies revise, and "they changed the number" is a finding.
    """
    __tablename__ = "metric_observations"
    __table_args__ = (
        UniqueConstraint("metric_id", "period", "document_id",
                         name="uq_metric_observations_metric_id_period_document_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    metric_id: Mapped[int] = mapped_column(
        ForeignKey("metrics.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"))
    period: Mapped[str64]
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    value: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    unit: Mapped[Optional[str64]]
    is_restated: Mapped[bool] = mapped_column(Boolean, default=False,
                                              server_default="false")

    metric: Mapped["Metric"] = relationship(back_populates="observations")


ALL_TABLES = (Company, Document, Chunk, Topic, TopicMention, TimelineEvent,
              Risk, Person, Metric, MetricObservation)
