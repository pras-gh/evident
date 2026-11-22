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

### `GET /v1/companies`

Every company with memory — what the home page lists.

```json
[{ "ticker": "NVDA", "name": "NVIDIA CORP", "cik": "0001045810",
   "document_count": 3, "latest_filing": "2026-02-25" }]
```

### `GET /v1/companies/{ticker}`

Memory summary — documents, entities per type, and when extraction last wrote.

```json
{ "company_id": 1, "cik": "0001045810", "ticker": "NVDA", "name": "NVIDIA CORP",
  "document_count": 3, "earliest_filing": "2024-02-21", "latest_filing": "2026-02-25",
  "counts": { "risk": 6, "geography": 4, "product": 3 },
  "built_at": "2026-09-24T00:36:46+05:30" }
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

Cards are **computed on read** from filings and entity mentions
(`parallx_memory.projection`); the card tables in `db/legacy-design` were never
migrated, and a derived read model does not need them. What that data can fill
today is **Risks** (risk entities cited in Item 1A of annual filings) and
**Litigation** (entities cited in Legal Proceedings). The others have no
history and say why in `unavailable` — Revenue and CapEx need metric values,
which nothing extracts yet; Products and Guidance route on earnings-call
transcripts, which are not ingested:

```json
{ "kind": "revenue", "revision_count": 0, "current": null,
  "unavailable": "Needs reported metric values, and nothing extracts them yet." }
```

The first revision of a card is a baseline and says "First on record: …",
never "N new …". Every fact's evidence carries `chunk_id` and `entity_slug`,
so it opens in the evidence viewer.

### `GET /v1/companies/{ticker}/cards/{kind}`

One card with its full `history`, oldest first. Add `?materially=true` to drop
the no-change revisions.

History is a separate call from the list on purpose: nine full trails is a lot
of payload for something most readers open one of.

### `GET /v1/companies/{ticker}/timeline`

`?limit=50&kind=promise`. The raw `timeline_events` log. For what changed
between filings — computed, with evidence — use `GET /v1/company/{ticker}/timeline`
(see `docs/timeline-engine.md`).

### `GET /v1/companies/{ticker}/promises`

**Not built — returns 501.** Designed and tested in `parallx_memory`, but
nothing extracts promises and their tables were never migrated.

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

When search cannot answer, it returns 503 with an `error` and a `message`
saying what to do:

| `error` | cause |
|---|---|
| `search_not_configured` | no `EMBEDDING_PROVIDER`, or an unknown one |
| `search_not_indexed` | no chunk is embedded with the configured provider and model; run `workers/embedding_worker.py` |
| `search_unavailable` | the provider failed to embed the query, e.g. a rate limit; retried once, quickly |

Search is scoped to the companies the query names: by rule for companies on
record, and by a model (task `classification`) for any company, including
ones with no filings here. A named company with no filings is never
substituted. If EDGAR has it, its latest annual report is fetched; see
`/v1/fetches`. The response says what was searched and why (`companies`,
`scope`, `topic`), and what kind of question it is (`kind`: `market`,
`filings` or `general`; `wants_filings`).

### `POST /v1/ask`

```json
{ "query": "why is nvidia down", "kind": "market", "chunk_ids": [30, 21, 18] }
```

An answer, from the live web or from the filings, never both in one answer.
The search page passes what search returned (`kind`, and the passages' chunk
ids), so the question is neither read nor searched twice.

- **`market` and `general` questions** are answered by Gemini with Google
  Search grounding (`mode: "web"`), with `sources` and Google's
  `search_suggestions` HTML. Google's terms require showing the suggestions
  with the answer, and forbid caching it or mixing other content into it. Paid
  Gemini keys only: a free key is refused, and the response says so.
- **`filings` questions** are answered from the given passages (at most 12),
  each claim citing them as `[n]` (`mode: "filings"`). `citations` maps each
  `n` to its chunk and paragraph; a marker for a passage not given is removed.
- **When the web is unavailable** and there are passages, the answer comes
  from those, and `notice` says so.

An answer that stopped early is never returned: `mode: "none"` with a `notice`.

### `GET /v1/fetches/{id}`

An on-demand fetch of a company's latest annual report from EDGAR, started by
a search that named it: `queued`, `running`, `done` or `failed` (with
`error`). The search page polls this, not the search, while it waits.

### Evidence

The evidence viewer's endpoints — see `docs/evidence-viewer.md` for the rules
behind them.

| | |
| --- | --- |
| `GET /v1/evidence/{chunk_id}?paragraph_id=&entity=` | one citation |
| `POST /v1/evidence/resolve` | up to 1,000 citations, order preserved; a bad one is flagged, not raised |
| `GET /v1/company/{ticker}/documents?form_type=&limit=` | a company's filings, newest first |
| `GET /v1/document/{id}` | a filing and every page: size, image and thumbnail URLs, PDF URL |
| `GET /v1/document/{id}/page/{page}` | one page: paragraphs, anchors, boxes, image, prev/next page with text |
| `GET /v1/document/{id}/page/{page}/image?size=full\|thumb` | the page as rendered (WebP), cached immutably |
| `GET /v1/document/{id}/pdf` | the filing as a PDF whose page N is page N |
| `GET /v1/documents/{id}/pages` | the whole filing as a paged reading view |

```json
{ "chunk_id": 22, "document_id": 1, "page": 27, "paragraph_id": "27_2",
  "anchors": ["p-27_2"],
  "bounding_box": null,
  "highlights": [{ "anchor": "p-27_2", "paragraph_id": "27_2", "page": 27,
                   "bounding_box": null }],
  "citation": "10-K · p. 27 · Item 1A. Risk Factors", "...": "..." }
```

A **bounding box** is `{page, x0, y0, x1, y1, page_width, page_height}` in
points from the page's top-left, y down. PDF filings get them at parse time;
HTML filings get them from the render worker, measured on the same rendering
the page images show — `null` until a filing has been rendered, when the
anchor is the target. Each highlight also carries `highlight_color`, the cited
entity's colour. A page inside the filing with
no stored text is an empty `200`; a page outside it is a `404` naming the range.

### `GET /health`

Liveness plus a real database round-trip.

## Errors

Standard FastAPI shape: `{"detail": "..."}`. `404` names the thing that was
missing; `501` names the document that explains what is not built yet.
