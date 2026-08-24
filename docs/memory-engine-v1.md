# Memory Engine V1

Production persistence layer: SQLAlchemy models, Alembic migrations, three
workers, and a query API.

## Status

In progress on `feat/memory-engine-v1`. See the PR.

## Scope

V1 is a deliberate narrowing. The core is nine tables — `companies`,
`documents`, `chunks`, `topics`, `topic_mentions`, `timeline_events`, `risks`,
`people`, `metrics` — on one Alembic chain. The memory-card and promise layers
built earlier become later revisions on the same chain rather than a second
schema.
