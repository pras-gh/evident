# Unified entity model

Replaces the per-type tables (`topics`, `people`, `products`, `risks`) with
three:

| Table | Purpose |
| --- | --- |
| `entities` | Canonical topics, people, products, metrics, risks |
| `entity_mentions` | Every place an entity appears |
| `relationships` | Edges between entities |

## Status

In progress on `feat/unified-entities`.
