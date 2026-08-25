# Legacy design SQL — not migrations

These three files are **not applied to anything**. They are the hand-written
schema that preceded the Alembic chain, kept because two of them contain design
work that has not been ported yet.

| File | Status |
| --- | --- |
| `001_substrate.sql` | Superseded — `db/alembic/versions/0001` covers it |
| `002_company_memory.sql` | **Not yet ported** — promises, products, events, evidence |
| `003_memory_cards.sql` | **Not yet ported** — memory cards, revisions, routing |

The promise lifecycle and the memory-card revision model are real designs with
tests behind them in `packages/memory/`, but they have no Alembic revision, so
the database cannot store them yet. Porting them is revisions `0005` and `0006`.

**Do not run these against a database.** They describe tables the current models
do not know about, and applying them alongside the Alembic chain produces a
schema that no code reads.

Migrations live in `db/alembic/versions/`. `db/schema.sql` is generated from
that chain.
