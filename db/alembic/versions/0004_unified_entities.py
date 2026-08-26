"""unified entities, entity_mentions and relationships

Replaces topics / topic_mentions / people / risks / metrics with one entity
table, one mention table and an edge table.

The reason is duplication: provenance columns had been added to five separate
tables, which is the same logic written five times and drifts. One mention
table means writing it once, and a new entity kind needs no schema change.

metric_observations deliberately survives as a typed side table, repointed at
entities. Observations carry a period and a numeric value — a time series, not
a mention — and folding `value` into JSONB would cost numeric ordering and
aggregation for nothing.

Data is migrated, not dropped: every topic, person, risk and metric becomes an
entity, and every topic mention becomes an entity mention.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("first_seen_at", sa.Date()),
        sa.Column("last_seen_at", sa.Date()),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_entities_company_id_companies",
                                ondelete="CASCADE"),
        sa.UniqueConstraint("company_id", "kind", "key",
                            name="uq_entities_company_id_kind_key"),
        sa.CheckConstraint("kind in ('topic','strategy','person','product',"
                           "'metric','risk','event','segment')",
                           name="ck_entities_kind"),
        sa.CheckConstraint("status in ('active','dropped','superseded')",
                           name="ck_entities_status"),
    )
    op.create_index("ix_entities_company_id", "entities", ["company_id"])
    op.create_index("ix_entities_kind", "entities", ["kind"])
    op.create_index("ix_entities_company_id_kind", "entities", ["company_id", "kind"])

    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_id", sa.BigInteger()),
        sa.Column("observed_at", sa.Date(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("paragraph_id", sa.String(64)),
        sa.Column("chunk_hash", sa.String(64)),
        sa.Column("confidence", sa.Float()),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"],
                                name="fk_entity_mentions_entity_id_entities",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"],
                                name="fk_entity_mentions_document_id_documents",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"],
                                name="fk_entity_mentions_chunk_id_chunks",
                                ondelete="CASCADE"),
        sa.UniqueConstraint("entity_id", "chunk_id",
                            name="uq_entity_mentions_entity_id_chunk_id"),
    )
    for cols, name in [(["entity_id"], "ix_entity_mentions_entity_id"),
                       (["document_id"], "ix_entity_mentions_document_id"),
                       (["chunk_id"], "ix_entity_mentions_chunk_id"),
                       (["paragraph_id"], "ix_entity_mentions_paragraph_id"),
                       (["entity_id", "observed_at"],
                        "ix_entity_mentions_entity_id_observed_at")]:
        op.create_index(name, "entity_mentions", cols)

    op.create_table(
        "relationships",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("source_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("target_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("document_ids", postgresql.ARRAY(sa.BigInteger()),
                  nullable=False, server_default="{}"),
        sa.Column("attributes", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("first_seen_at", sa.Date()),
        sa.Column("last_seen_at", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_relationships_company_id_companies",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_entity_id"], ["entities.id"],
                                name="fk_relationships_source_entity_id_entities",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_entity_id"], ["entities.id"],
                                name="fk_relationships_target_entity_id_entities",
                                ondelete="CASCADE"),
        sa.UniqueConstraint("source_entity_id", "target_entity_id", "kind",
                            name="uq_relationships_source_target_kind"),
        sa.CheckConstraint("source_entity_id <> target_entity_id",
                           name="ck_relationships_no_self_edge"),
    )
    op.create_index("ix_relationships_company_id", "relationships", ["company_id"])
    op.create_index("ix_relationships_company_id_kind", "relationships",
                    ["company_id", "kind"])
    op.create_index("ix_relationships_source_entity_id", "relationships",
                    ["source_entity_id"])
    op.create_index("ix_relationships_target_entity_id", "relationships",
                    ["target_entity_id"])

    # ---- carry the data across -----------------------------------------
    op.execute("""
        INSERT INTO entities (company_id, kind, key, label, attributes, status,
                              first_seen_at, last_seen_at, mention_count)
        SELECT company_id, 'topic', slug, label, '{}'::jsonb, 'active',
               first_seen_at, last_seen_at, mention_count
          FROM topics
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO entities (company_id, kind, key, label, attributes, status,
                              first_seen_at, last_seen_at)
        SELECT company_id, 'person', normalised, full_name,
               jsonb_strip_nulls(jsonb_build_object('roles', roles)),
               'active', first_seen_at, last_seen_at
          FROM people
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO entities (company_id, kind, key, label, attributes, status,
                              first_seen_at, last_seen_at)
        SELECT company_id, 'risk', slug, label,
               jsonb_strip_nulls(jsonb_build_object('category', category,
                                                    'severity', severity)),
               status, first_seen_at, last_seen_at
          FROM risks
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO entities (company_id, kind, key, label, attributes, status)
        SELECT company_id, 'metric', normalised, name,
               jsonb_strip_nulls(jsonb_build_object('unit', unit)), 'active'
          FROM metrics
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO entity_mentions (entity_id, document_id, chunk_id, observed_at,
                                     quote, page_number, paragraph_id, confidence)
        SELECT e.id, tm.document_id, tm.chunk_id, tm.observed_at, tm.quote,
               tm.page_number, tm.paragraph_id, tm.confidence
          FROM topic_mentions tm
          JOIN topics t ON t.id = tm.topic_id
          JOIN entities e ON e.company_id = t.company_id
                         AND e.kind = 'topic' AND e.key = t.slug
        ON CONFLICT DO NOTHING
    """)

    # ---- repoint the survivors -----------------------------------------
    op.add_column("metric_observations",
                  sa.Column("entity_id", sa.BigInteger()))
    op.execute("""
        UPDATE metric_observations mo
           SET entity_id = e.id
          FROM metrics m
          JOIN entities e ON e.company_id = m.company_id
                         AND e.kind = 'metric' AND e.key = m.normalised
         WHERE mo.metric_id = m.id
    """)
    op.execute("DELETE FROM metric_observations WHERE entity_id IS NULL")
    op.alter_column("metric_observations", "entity_id", nullable=False)
    op.drop_constraint("uq_metric_observations_metric_id_period_document_id",
                       "metric_observations", type_="unique")
    op.drop_column("metric_observations", "metric_id")
    op.create_foreign_key("fk_metric_observations_entity_id_entities",
                          "metric_observations", "entities",
                          ["entity_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_metric_observations_entity_id_period_document_id",
        "metric_observations", ["entity_id", "period", "document_id"])
    op.create_index("ix_metric_observations_entity_id", "metric_observations",
                    ["entity_id"])

    op.add_column("timeline_events", sa.Column("entity_id", sa.BigInteger()))
    op.execute("""
        UPDATE timeline_events te
           SET entity_id = e.id
          FROM topics t
          JOIN entities e ON e.company_id = t.company_id
                         AND e.kind = 'topic' AND e.key = t.slug
         WHERE te.topic_id = t.id
    """)
    op.drop_index("ix_timeline_events_topic_id", table_name="timeline_events")
    op.drop_column("timeline_events", "topic_id")
    op.create_foreign_key("fk_timeline_events_entity_id_entities",
                          "timeline_events", "entities",
                          ["entity_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_timeline_events_entity_id", "timeline_events", ["entity_id"])

    for table in ("topic_mentions", "topics", "people", "risks", "metrics"):
        op.drop_table(table)


def downgrade() -> None:
    raise NotImplementedError(
        "0004 collapses five tables into three and cannot be reversed without "
        "losing the entity kinds that had no table of their own — strategy, "
        "product, segment. Restore from a backup taken before the upgrade."
    )
