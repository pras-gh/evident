# API

Read-only HTTP over the three layers. Base path `/v1`. JSON throughout.

Every response that carries a claim also carries the evidence for it. That is
enforced by the response models rather than by each endpoint's discretion —
an endpoint that forgets to include evidence is indistinguishable from one
that had none to give.

## Conventions

| | |
| --- | --- |
| Company key | ticker, case-insensitive (`AAPL`, `aapl`) |
| Dates | ISO 8601 date, `2025-02-01` |
| Missing memory | `404` with the ticker named, not an empty `200` |
| Not yet built | `501` with a pointer to this document |

## Endpoints

### `GET /v1/companies/{ticker}`

Memory summary — document count and per-entity counts.

```json
{ "company_id": "0000320193", "ticker": "AAPL", "document_count": 1248,
  "counts": { "topics": 34, "people": 12, "metrics": 61, "risks": 12,
              "promises": 5, "products": 7, "events": 28 },
  "built_at": "2025-02-01" }
```

### `GET /v1/companies/{ticker}/cards`

Every memory card with its **current** revision. `source_label` is the routing
binding — the "Updates from" column.

```json
[{ "kind": "capex", "title": "CapEx", "source_label": "Cash Flow section",
   "revision_count": 4, "material_count": 3, "last_updated_at": "2025-02-01",
   "current": { "revision": 4, "as_of": "2025-02-01",
                "summary": "CapEx rose from 10,922 to 14,602.",
                "is_material": true, "facts": [...], "delta": {...},
                "evidence": [...] } }]
```

`revision_count` and `material_count` differ on purpose. A filing that touched
a card without moving anything still earns a revision, marked immaterial, so a
client can say "4 updates, 3 material" rather than implying every filing
mattered.

### `GET /v1/companies/{ticker}/cards/{kind}`

One card with its full `history`, oldest first. Add `?materially=true` to drop
the no-change revisions.

History is a separate call from the list on purpose: nine full trails is a lot
of payload for something most readers open one of.

### `GET /v1/companies/{ticker}/timeline`

`?limit=50&kind=promise`. The materialised spine over every dated entity.

### `GET /v1/companies/{ticker}/promises`

`?status=open|kept|broken|abandoned|unclear`.

`unclear` means the horizon passed and nothing in a later filing settled it. It
is deliberately distinct from `broken` — silence is not evidence of failure, and
an unresolved commitment is itself worth surfacing. A `broken` promise always
carries `resolved_evidence`.

### `GET /v1/companies/{ticker}/graph`

`?min_co_occurrence=2&until=2024-06-01`

Topic graph. Edges are weighted by **shared documents**, not text similarity, and
each carries the document ids that justify it — an edge you cannot explain is
decoration. `until` returns the graph as it stood on that date, which is what
the replay animation scrubs.

### `POST /v1/search`

```json
{ "query": "why did capital expenditure increase", "ticker": "AAPL",
  "form_types": ["10-K"], "k": 20 }
```

Vector search over evidence spans. A supporting index, not the product: every
hit carries the document, page and paragraph ids it came from.

Ranking is hybrid — cosine similarity finds the passage, recency breaks
near-ties. A 10-K and a stale 10-Q often carry the same sentence, and the newer
one is almost always what was wanted.

### `GET /health`

Liveness plus a real database round-trip.

## Errors

Standard FastAPI shape: `{"detail": "..."}`. `404` names the thing that was
missing; `501` names the document that explains what is not built yet.
