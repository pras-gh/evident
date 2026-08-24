# Unified entity model and the Memory Graph

Replaces the per-type tables (`topics`, `people`, `products`, `risks`,
`metrics`) with three.

| Table | Purpose |
| --- | --- |
| `entities` | Canonical topics, strategies, people, products, metrics, risks |
| `entity_mentions` | Every place an entity appears |
| `relationships` | Edges between entities |

## Why

Provenance columns had been added to five separate tables — the same logic
written five times, which drifts. One mention table means writing it once, and a
new entity kind is now a row rather than a migration.

`relationships` is also a capability that did not exist: the graph was
recomputed by co-occurrence at query time, so typed edges could not be stored at
all.

### One carve-out

`metric_observations` survives as a typed side table, repointed at `entities`.

| former table | type-specific columns |
| --- | --- |
| topics | *(none)* |
| people | `roles` |
| risks | `category`, `severity`, `status` |
| metrics | `unit` |
| **metric_observations** | `period`, `period_end`, **`value` (numeric)**, `is_restated` |

The first four fold into a JSONB `attributes` column cleanly. Observations are a
numeric time series rather than a mention, and putting `value` in JSONB would
cost numeric ordering and aggregation for nothing.

`status` is a real column on `entities`, not an attribute, because a risk that
stops being disclosed is a finding people query for.

## Memory Graph API — frozen

```
GET /v1/company/{ticker}/graph
```

```json
{
  "company": "NVDA",
  "nodes": [
    { "id": "ai_infrastructure", "label": "AI Infrastructure",
      "type": "strategy", "importance": 100, "mentions": 68 }
  ],
  "edges": [
    { "source": "blackwell", "target": "ai_infrastructure",
      "relationship": "drives_investment", "strength": 0.5 }
  ]
}
```

**Node ids are entity keys, not database ids.** Clients cache this graph and
hold those ids; a surrogate key would change on a rebuild and break every stored
reference silently.

Additive fields may appear. Renaming, removing, or changing the meaning of
`id`, `label`, `type`, `importance`, `mentions`, `source`, `target`,
`relationship` or `strength` is a breaking change, and
`tests/test_graph_contract.py` fails on it.

## Topic normalization

A filing says "AI infrastructure", "artificial intelligence infrastructure",
"A.I. infrastructure" and "accelerated computing" for one thing. Without
folding, the graph shows four weak nodes instead of one strong one — and
importance, which is mostly frequency, under-reports all four.

`entity_key()` expands abbreviations, drops articles and applies whole-label
aliases. Keys use underscores because the contract's node ids do.

`display_label()` gives folded entities a fixed display name. Without it the
label a reader sees depends on ingest order — whichever variant happened to land
last.

## Importance scoring

A weighted sum of four components, each stored alongside the score:

| component | weight | why |
| --- | --- | --- |
| frequency | 0.35 | log-scaled, so a topic mentioned 400 times does not flatten the rest to zero |
| spread | 0.25 | across distinct filings — something raised every quarter beats something discussed once at length |
| recency | 0.20 | half-life of ~18 months; a topic last seen in 2019 is history, not strategy |
| centrality | 0.20 | a topic touching many others is structurally important even when mentioned less |

Maxima are corpus-relative, so scoring runs over a whole company at once.
Scoring an entity in isolation would make importance incomparable between
companies and unstable as the corpus grows.

`GET /v1/company/{ticker}/graph/nodes/{node_id}` returns the components and the
raw signals. **A score nobody can interrogate is the same failure as an uncited
claim**, and the contract returns a bare number.

## Relationships

Two sources.

**Co-occurrence** is derived — two entities in the same filing are related,
weighted by shared filings. Cheap, always available, and explainable: the edge
names the documents that produced it.

**Typed edges** come from extraction (`blackwell -> drives_investment ->
ai_infrastructure`). These say what co-occurrence cannot, and they are also what
a model can invent — so an extracted edge is kept only when **both endpoints
already exist as entities**. An edge to something never mentioned is dropped,
not stored.

Edges are a materialisation of what `entity_mentions` already says, so the table
can be rebuilt and is not a second source of truth.

## Migration

`0004` moves the data rather than dropping it: every topic, person, risk and
metric becomes an entity, and every topic mention becomes an entity mention.
Verified on seeded rows — the dropped-risk status, a person's roles, a metric's
unit and a mention's page/paragraph/confidence all survive.

It has **no downgrade**. Collapsing five tables into three cannot be reversed
without losing the kinds that never had a table of their own (strategy, product,
segment), and a downgrade that silently discarded them would be worse than one
that refuses.
