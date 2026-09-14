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

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime,
                        Float, ForeignKey, Index, Integer, Numeric, String,
                        Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from evident_graph.taxonomy import check_constraint

from .base import Base, created_at, str16, str64, str255, updated_at

#: The one width every supported embedding provider can emit. It is 1024
#: because `voyage-finance-2` is 1024-only; OpenAI shortens to match. Changing
#: it means a migration and a full re-embed, never a constant edit.
EMBEDDING_DIM = 1024


# `strategy` is in the graph contract's node types, so it is a first-class kind
# rather than a topic wearing a label.
ENTITY_KINDS = ("topic", "strategy", "person", "product", "metric", "risk",
                "event", "segment")
RELATIONSHIP_KINDS = ("co_occurs", "mentioned_with", "holds_role", "affects",
                      "supersedes", "part_of")


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
    entities: Mapped[list["Entity"]] = relationship(
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

      * `chunk_hash` identifies this chunk globally. Derived from
        company + accession + page + normalised text, so the same words on the
        same page of the same filing are the same chunk however often we
        re-ingest.
      * `paragraph_ids` lists the source paragraphs it was built from. This is
        what lets an answer cite "paragraph 3" rather than gesturing at a span
        of text somebody assembled.

    An oversized paragraph is split into parts keyed `p_abc#1`, `p_abc#2`, so a
    part still resolves to its source paragraph.
    """
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("chunk_hash", name="uq_chunks_chunk_hash"),
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
    chunk_hash: Mapped[str] = mapped_column(String(64))
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
    entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str64] = mapped_column(index=True)
    headline: Mapped[str] = mapped_column(Text)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    occurred_at: Mapped[date] = mapped_column(Date)
    ref: Mapped[str255]
    page_number: Mapped[Optional[int]]
    paragraph_id: Mapped[Optional[str64]]
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[created_at]





class MetricObservation(Base):
    """One row per (metric, period, document).

    Including `document_id` in the key is deliberate: a second observation of
    the same period from a *later* filing is a restatement, not a duplicate.
    Companies revise, and "they changed the number" is a finding.
    """
    __tablename__ = "metric_observations"
    __table_args__ = (
        UniqueConstraint("entity_id", "period", "document_id",
                         name="uq_metric_observations_entity_id_period_document_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"))
    period: Mapped[str64]
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    value: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    unit: Mapped[Optional[str64]]
    page_number: Mapped[Optional[int]]
    paragraph_id: Mapped[Optional[str64]]
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    is_restated: Mapped[bool] = mapped_column(Boolean, default=False,
                                              server_default="false")




class Entity(Base):
    """Canonical topics, people, products, metrics and risks in one table.

    Identity is `(company_id, slug)` where `slug` is the normalised form of the
    name. The unique constraint is what makes the builder update rather than
    duplicate, and it covers every type instead of being re-stated per table.

    The scope is per company on purpose. A globally unique slug would work for
    exactly one filer: every company on earth reports `revenue`, and the second
    one ingested would collide on the first slug it shares. Scoping to the
    company also means a name resolves to one type per company — `Data Center`
    cannot be a segment in one filing and a product in the next.

    Type-specific data lives in `attributes`: a person's dated roles, a risk's
    category, a metric's unit. `status` is deliberately a real column rather
    than an attribute, because a risk that stops being disclosed is a finding
    people query for.
    """
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("company_id", "slug",
                         name="uq_entities_company_id_slug"),
        # generated from the same tuple that builds the prompt and the schema
        # enum, so a type cannot be extractable but unstorable
        CheckConstraint(check_constraint("entity_type"), name="entity_type"),
        CheckConstraint("status in ('active','dropped','superseded')",
                        name="status"),
        CheckConstraint("importance_score between 0 and 100",
                        name="importance_score"),
        Index("ix_entities_company_id_entity_type", "company_id", "entity_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str16] = mapped_column(index=True)
    slug: Mapped[str255]
    name: Mapped[str255]
    description: Mapped[Optional[str]] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict,
                                             server_default="{}")
    status: Mapped[str16] = mapped_column(default="active",
                                          server_default="active")
    first_seen: Mapped[Optional[date]] = mapped_column(Date)
    latest_seen: Mapped[Optional[date]] = mapped_column(Date)
    mention_count: Mapped[int] = mapped_column(Integer, default=0,
                                               server_default="0")
    # computed corpus-relative by the graph engine and persisted here, so a
    # client can rank without recomputing the whole graph
    importance_score: Mapped[float] = mapped_column(Float, default=0.0,
                                                    server_default="0")
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    company: Mapped["Company"] = relationship(back_populates="entities")
    mentions: Mapped[list["EntityMention"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan")


class EntityMention(Base):
    """Every place an entity appears — one provenance shape for every kind.

    Previously this was `topic_mentions` plus the same three provenance columns
    bolted onto four other tables. Writing it once means a new entity kind
    needs no schema change and cannot quietly ship without provenance.
    """
    __tablename__ = "entity_mentions"
    __table_args__ = (
        UniqueConstraint("entity_id", "chunk_id",
                         name="uq_entity_mentions_entity_id_chunk_id"),
        Index("ix_entity_mentions_entity_id_observed_at",
              "entity_id", "observed_at"),
        Index("ix_entity_mentions_paragraph_id", "paragraph_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    observed_at: Mapped[date] = mapped_column(Date)
    quote: Mapped[str] = mapped_column(Text)
    page: Mapped[Optional[int]]
    paragraph_id: Mapped[Optional[str64]]
    chunk_hash: Mapped[Optional[str]] = mapped_column(String(64))
    # the extractor's own reported score — a self-report, not a calibrated
    # probability. Fine for ranking and triage; not a likelihood of truth.
    confidence: Mapped[Optional[float]] = mapped_column(Float)

    entity: Mapped["Entity"] = relationship(back_populates="mentions")


class Relationship(Base):
    """A typed edge between two entities.

    Edges are derivable from `entity_mentions` — two entities sharing a
    document co-occur — so this table is a materialisation, not a second source
    of truth, and can be rebuilt. `document_ids` records what produced an edge,
    because an edge you cannot explain is decoration.
    """
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "relationship_type",
                         name="uq_relationships_source_target_type"),
        CheckConstraint("source_entity_id <> target_entity_id", name="no_self_edge"),
        CheckConstraint("strength between 0 and 1", name="strength"),
        Index("ix_relationships_company_id_relationship_type",
              "company_id", "relationship_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    source_entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    target_entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    relationship_type: Mapped[str64]
    # normalised 0-1, matching what the frozen graph contract emits
    strength: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    # the single chunk a reader should be shown to justify this edge; the
    # array keeps the full set, because one edge is usually built from many
    evidence_chunk_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"), index=True)
    document_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger),
                                                    default=list,
                                                    server_default="{}")
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict,
                                             server_default="{}")
    first_seen: Mapped[Optional[date]] = mapped_column(Date)
    latest_seen: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


class ExtractionRun(Base):
    """One extraction run: what it processed, what it cost, how long it took.

    The row you compare across time. `extraction_calls` holds the per-request
    detail behind these totals, including the raw responses; this table answers
    "what did run X cost and how much of it was accepted" without reading
    fourteen rows and summing them.

    `provider` sits next to `model` because they move independently — the same
    model served through a different provider can price and behave differently,
    and a benchmark that records only the model cannot explain the difference.

    `cost_usd` is NUMERIC, not float. It is money; binary floating point makes
    totals that do not add up, and a benchmark whose costs disagree with the
    invoice is worse than one with no costs at all.

    `finished_at` is nullable on purpose: a row is written when the run starts,
    so a run that crashes leaves evidence rather than vanishing. A null finish
    means it did not complete.
    """
    __tablename__ = "extraction_runs"
    __table_args__ = (
        CheckConstraint("chunks_accepted + chunks_rejected <= chunks_processed",
                        name="chunk_counts"),
        CheckConstraint("cost_usd >= 0", name="cost_usd"),
        Index("ix_extraction_runs_document_id_started_at",
              "document_id", "started_at"),
    )

    #: A UUID, generated by the caller before the first request, so calls can
    #: reference their run before the run has finished.
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)

    provider: Mapped[str64]
    model: Mapped[str64]
    #: the prompt that produced it — a rate change with no prompt or model
    #: change is a signal about the model, and telling those apart needs both
    prompt_id: Mapped[Optional[str64]]

    # documents.id is BIGINT, so this cannot be the UUID the spec asked for
    # without retyping every primary key in the schema.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True)

    chunks_processed: Mapped[int] = mapped_column(Integer, default=0,
                                                  server_default="0")
    chunks_accepted: Mapped[int] = mapped_column(Integer, default=0,
                                                 server_default="0")
    chunks_rejected: Mapped[int] = mapped_column(Integer, default=0,
                                                 server_default="0")

    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0,
                                                     server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0,
                                              server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0,
                                               server_default="0")

    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"),
                                              server_default="0")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    @property
    def acceptance_rate(self) -> float:
        return (self.chunks_accepted / self.chunks_processed
                if self.chunks_processed else 0.0)

    @property
    def duration_s(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class ExtractionCall(Base):
    """One Claude call, kept verbatim.

    The raw response is stored because three questions are otherwise
    unanswerable. Whether a schema change would have caught more entities —
    replayable offline against these rows, for free. Whether a drop-rate change
    came from the prompt or the model — both versions are on the row. And
    whether a wrong entity in the graph was the model's error or ours — without
    the original text those are indistinguishable, so the pipeline gets blamed
    for prompt problems and vice versa.

    Rejected responses are stored too. A response that failed to parse is the
    most useful artefact there is when working out why.
    """
    __tablename__ = "extraction_calls"
    __table_args__ = (
        UniqueConstraint("run_id", "chunk_id", name="uq_extraction_calls_run_id_chunk_id"),
        CheckConstraint("status in ('accepted','rejected')", name="status"),
        Index("ix_extraction_calls_run_id_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: the run this call belongs to. Totals live on the run row; this table is
    #: the per-call detail behind them.
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("extraction_runs.id", ondelete="CASCADE"),
        index=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"), index=True)

    #: name@version — a prompt change must be visible as the cause of a rate change
    prompt_id: Mapped[str64]
    model: Mapped[str64]

    status: Mapped[str16]
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    stop_reason: Mapped[Optional[str16]]
    #: exactly what came back, unparsed
    raw_response: Mapped[Optional[str]] = mapped_column(Text)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_created_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    latency_ms: Mapped[Optional[int]]

    # returned vs kept is the acceptance rate, per chunk
    entities_returned: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    entities_kept: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    relationships_returned: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    relationships_kept: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[created_at]


ALL_TABLES = (Company, Document, Chunk, Entity, EntityMention, Relationship,
              TimelineEvent, MetricObservation, ExtractionRun,
              ExtractionCall)
