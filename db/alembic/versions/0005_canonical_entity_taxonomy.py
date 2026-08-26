"""canonical entity taxonomy and the spec column names

Renames the entity tables onto the Phase 1 spec and closes the type set to
eight canonical types.

Three things here are not pure renames and are worth reading before running it.

**`topic` folds into `strategy`.** `topic` was the catch-all; in the new
taxonomy every one of them is a strategy. `person` becomes `executive`. Both are
meaning-preserving.

**`event` has no home in the new set** and is not silently retyped. Events
already have their own table with a period and an outcome; forcing them into a
type they do not belong to would put wrong data in front of a reader. If any
exist the migration stops and says so, rather than guessing.

**Identity narrows from `(company_id, kind, key)` to `(company_id, slug)`.**
Folding topic into strategy can therefore collide — a corpus holding both a
`topic` and a `strategy` for `ai_infrastructure` now has two rows claiming one
identity. They are merged, not dropped: mentions repoint to the survivor,
counts sum, and the date range widens to cover both.

`relationships` is emptied rather than merged. It is a materialisation of
`entity_mentions`, not a source of truth — the model says so — and rebuilding it
is a graph-builder run, whereas merging edges across merged endpoints means
reconciling unique constraints and self-edges for data that is already
derivable. **The graph has no edges until the builder runs again.**

`weight` is dropped in favour of `strength`, which is what the frozen graph
contract already emits. Nothing is lost: `weight` was the number of documents
behind an edge, which is `cardinality(document_ids)`.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CANONICAL = ("strategy", "product", "executive", "risk", "metric", "segment",
             "company", "geography")


def upgrade() -> None:

    # ---------------------------------------------------------------- guard
    # Before anything is renamed, so the message names the column an operator
    # can actually go and look at.
    #
    # `--sql` (offline) rendering cannot query anything: op.get_bind() hands
    # back a connection whose execute() returns None rather than raising, so a
    # None test on the bind does not catch it -- `as_sql` on the migration
    # context is what distinguishes the two modes. Skipping the guard there is
    # the right trade: offline mode emits DDL for review and never touches a
    # database, so there is no data to strand. The guard runs on every real
    # upgrade, which is where it matters.
    if not op.get_context().as_sql:
        stranded = op.get_bind().execute(sa.text(
            "select count(*) from entities where kind = 'event'")).scalar_one()
        if stranded:
            raise RuntimeError(
                f"{stranded} entities still have kind='event', which has no "
                "equivalent in the canonical taxonomy. Events belong in "
                "timeline_events. Move or delete them, then re-run this "
                "migration. Retyping them automatically would store a claim "
                "the filing does not make."
            )

    # --------------------------------------------------------- entities
    # The check constraint is named ck_entities_ck_entities_kind in the
    # database: 0004 passed an already-prefixed name and the ck convention
    # (ck_%(table_name)s_%(constraint_name)s) prefixed it again. drop_constraint
    # re-applies that same template, so the inner name is what goes here --
    # passing the full database name would ask for a third prefix. Unique and
    # index names are not re-templated and are passed literally.
    op.drop_constraint("uq_entities_company_id_kind_key", "entities",
                       type_="unique")
    op.drop_constraint("ck_entities_kind", "entities", type_="check")
    op.drop_index("ix_entities_company_id_kind", table_name="entities")

    op.alter_column("entities", "kind", new_column_name="entity_type")
    op.alter_column("entities", "key", new_column_name="slug")
    op.alter_column("entities", "label", new_column_name="name")
    op.alter_column("entities", "first_seen_at", new_column_name="first_seen")
    op.alter_column("entities", "last_seen_at", new_column_name="latest_seen")

    # ALTER COLUMN ... RENAME leaves the index in place under its old name, so
    # the schema would carry ix_entities_kind on a column called entity_type.
    op.execute("alter index ix_entities_kind rename to ix_entities_entity_type")

    op.add_column("entities", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("entities", sa.Column("importance_score", sa.Float(),
                                        nullable=False, server_default="0"))

    op.execute("update entities set entity_type = 'strategy' "
               "where entity_type = 'topic'")
    op.execute("update entities set entity_type = 'executive' "
               "where entity_type = 'person'")

    # ------------------------------------------------- merge slug collisions
    # Folding topic into strategy can produce two rows for one identity.
    op.execute("""
        create temporary table _entity_merge on commit drop as
        select id as dup_id,
               first_value(id) over (partition by company_id, slug
                                     order by id) as keep_id
        from entities
    """)

    # A repointed mention can collide with one the survivor already has, and
    # UPDATE would abort on the unique constraint — so drop the duplicates
    # first. Nothing is lost: it is the same quote on the same chunk.
    op.execute("""
        delete from entity_mentions m
        using _entity_merge x, entity_mentions k
        where m.entity_id = x.dup_id
          and x.dup_id <> x.keep_id
          and k.entity_id = x.keep_id
          and k.chunk_id is not distinct from m.chunk_id
    """)
    op.execute("""
        update entity_mentions m set entity_id = x.keep_id
        from _entity_merge x
        where m.entity_id = x.dup_id and x.dup_id <> x.keep_id
    """)
    op.execute("""
        update metric_observations o set entity_id = x.keep_id
        from _entity_merge x
        where o.entity_id = x.dup_id and x.dup_id <> x.keep_id
    """)

    # Carry the survivor's counts and date range over the merged set.
    op.execute("""
        update entities e
        set mention_count = agg.mention_count,
            first_seen    = agg.first_seen,
            latest_seen   = agg.latest_seen,
            attributes    = agg.attributes
        from (
            select x.keep_id,
                   sum(d.mention_count)                       as mention_count,
                   min(d.first_seen)                          as first_seen,
                   max(d.latest_seen)                         as latest_seen,
                   jsonb_object_agg(k, v)
                       filter (where k is not null)           as attributes
            from _entity_merge x
            join entities d on d.id = x.dup_id
            left join lateral jsonb_each(d.attributes) as a(k, v) on true
            group by x.keep_id
        ) agg
        where e.id = agg.keep_id
    """)
    op.execute("""
        delete from entities e
        using _entity_merge x
        where e.id = x.dup_id and x.dup_id <> x.keep_id
    """)

    op.create_unique_constraint("uq_entities_company_id_slug", "entities",
                                ["company_id", "slug"])
    op.create_check_constraint(
        "entity_type", "entities",
        "entity_type in (" + ",".join(f"'{t}'" for t in CANONICAL) + ")")
    op.create_check_constraint("importance_score", "entities",
                               "importance_score between 0 and 100")
    op.create_index("ix_entities_company_id_entity_type", "entities",
                    ["company_id", "entity_type"])

    # --------------------------------------------------- entity_mentions
    op.alter_column("entity_mentions", "page_number", new_column_name="page")

    # ----------------------------------------------------- relationships
    # Derived, and cheaper to rebuild than to merge across merged endpoints.
    op.execute("delete from relationships")

    op.drop_constraint("uq_relationships_source_target_kind",
                       "relationships", type_="unique")
    op.drop_index("ix_relationships_company_id_kind", table_name="relationships")

    op.alter_column("relationships", "kind",
                    new_column_name="relationship_type")
    op.alter_column("relationships", "first_seen_at",
                    new_column_name="first_seen")
    op.alter_column("relationships", "last_seen_at",
                    new_column_name="latest_seen")
    op.drop_column("relationships", "weight")
    op.add_column("relationships", sa.Column("strength", sa.Float(),
                                             nullable=False, server_default="0"))
    op.add_column("relationships", sa.Column("evidence_chunk_id", sa.BigInteger(),
                                            nullable=True))
    op.create_foreign_key("fk_relationships_evidence_chunk_id_chunks",
                          "relationships", "chunks",
                          ["evidence_chunk_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_relationships_evidence_chunk_id", "relationships",
                    ["evidence_chunk_id"])

    op.create_unique_constraint("uq_relationships_source_target_type",
                                "relationships",
                                ["source_entity_id", "target_entity_id",
                                 "relationship_type"])
    op.create_check_constraint("strength", "relationships",
                               "strength between 0 and 1")
    op.create_index("ix_relationships_company_id_relationship_type",
                    "relationships", ["company_id", "relationship_type"])


def downgrade() -> None:
    raise NotImplementedError(
        "0005 folds topic into strategy and merges entities that then share a "
        "slug. The pre-merge split cannot be reconstructed from the merged "
        "row, and the dropped relationships were derived. Restore from a "
        "backup taken before the upgrade, then re-run the graph builder."
    )
