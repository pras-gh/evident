# Memory Engine V1

Production persistence for company memory: SQLAlchemy models, one Alembic
chain, three workers, and a query API.

## Stack

FastAPI · PostgreSQL 17 · pgvector 0.8 · SQLAlchemy 2.0 · Alembic

## Schema

Ten tables on one migration chain.

| Table | Holds |
| --- | --- |
| `companies` | CIK, ticker, name |
| `documents` | one filing; carries the EDGAR **acceptance** timestamp and a content digest |
| `chunks` | paragraph-level spans with page number, section title and the embedding |
| `topics` | unique per company by slug |
| `topic_mentions` | topic ↔ chunk, with the quote |
| `timeline_events` | the materialised spine |
| `risks` | `status` flips to `dropped`, rows are never deleted |
| `people` | `normalised` is identity, `full_name` is display |
| `metrics` / `metric_observations` | a series per metric; restatements flagged |

`metrics` is not in the V1 table list, but deliverable 4 extracts metrics and
they need somewhere to live.

### Why the constraints are the design

"Update existing memory instead of duplicating topics" is **not** implemented as
an application-level check. It is a unique constraint on `(company_id, slug)`
plus `ON CONFLICT DO UPDATE` in the repository layer.

The difference matters under concurrency. A select-then-insert would race two
ingest workers into duplicate topics the moment anything runs in parallel, and
the duplicate would look like a legitimate second topic rather than a bug.
Pushing it into the database makes duplication *impossible* instead of merely
unlikely.

The same pattern covers companies, risks, people, metrics, and timeline events.
`first_seen_at` uses `least()` and `last_seen_at` uses `greatest()`, so ingesting
filings out of order still produces the right span.

## Workers

| Worker | Reads | Writes | Idempotent on |
| --- | --- | --- | --- |
| `ingest_worker` | EDGAR | documents, chunks | accession + content digest |
| `embedding_worker` | chunks without vectors | `chunks.embedding` | presence of a vector |
| `memory_builder` | chunks | topics, mentions, risks, people, metrics, events | natural entity identity |

They are separate processes on purpose: a re-embed never forces a re-parse, and
a provider change is a backfill rather than a migration.

### The embedding worker refuses two things

**A dimension mismatch.** If the embedder's dimension does not match the column,
it raises rather than truncating or padding. A silently reshaped vector poisons
the index in a way that only ever shows up as quietly worse search results.

**A misaligned batch.** If the provider returns a different number of vectors
than chunks sent, it aborts. Writing vector *n* against chunk *n+1* would attach
confident citations to the wrong text, which is the worst failure this product
has.

## API

```
GET  /v1/companies/{ticker}/memory          company memory
GET  /v1/companies/{ticker}/topics          topics
GET  /v1/companies/{ticker}/topics/{slug}   one topic, every mention, each cited
GET  /v1/companies/{ticker}/timeline        timeline
GET  /v1/companies/{ticker}/risks           ?status=active|dropped
POST /v1/search                             semantic search
GET  /health                                liveness + a real database round-trip
```

Citation fields are **required** on `MentionOut` and `SearchHitOut`, not
optional. A response that *can* omit provenance eventually will.

An unknown ticker is `404`, not an empty `200` — an empty list reads as "we
looked and there is nothing", which is a different and more misleading claim
than "we have never heard of this company".

## Ingestion

```
POST /v1/ingest
{ "ticker": "NVDA", "form_types": ["10-K"], "limit": 1 }
```

Runs inline and is bounded by `limit` — honest for V1 sizes, and the obvious
thing to move onto a queue, since a twenty-filing backfill will outlive a
sensible HTTP timeout.

Requires `SEC_USER_AGENT`; the endpoint returns **503** rather than making an
undeclared request, because SEC asks automated traffic to identify itself and
silently ignoring that is not ours to decide.

### The origin is configurable

`SEC_WWW_URL`, `SEC_DATA_URL` and `SEC_ARCHIVES_URL` override the fetch layer's
base URLs. This is not a test hook bolted on afterwards: **SEC blocks whole IP
ranges at its edge**, returning a 403 Fair Access page even to a well-behaved
client with a declarative User-Agent. Any environment behind such a range — CI,
a corporate egress, a cloud region — needs to point at a cache or mirror, and a
hard-coded origin would make those environments not merely inconvenient but
untestable.

`tests/test_ingest_e2e.py` uses the same override against a fixture origin, so
the whole ingest path stays covered without depending on SEC being reachable.

## Running

```bash
createdb evident && psql evident -c 'create extension vector'
export DATABASE_URL=postgresql+psycopg://localhost/evident
cd db && alembic upgrade head

python -m workers.ingest_worker    --cik 1045810 --accession 0001045810-25-000023
python -m workers.embedding_worker
python -m workers.memory_builder   --company-id 1

uvicorn api.main:app --reload
```

## Verification

Run against real PostgreSQL 17.11 + pgvector 0.8.6:

- `alembic upgrade head` on a **fresh** database creates all ten tables plus the
  HNSW index, and `downgrade base` → `upgrade head` round-trips cleanly
- **13 integration tests** covering the upserts, the embedding worker and all
  four endpoints
- **67 stdlib tests** still pass with nothing installed

```bash
export TEST_DATABASE_URL=postgresql+psycopg://localhost/evident_test
python -m unittest tests.test_engine_e2e
```

## Completeness

Verified against the real NVDA FY2025 10-K (`nvda-20250126.htm`, 2.08 MB),
measured in a browser because SEC blocks this network at its edge:

| | |
| --- | --- |
| pages parsed | **87** |
| sections parsed | **27** |
| paragraphs parsed | **1,132** |
| chunks created | **275** (204 prose + 71 table) |
| tables extracted | **68** |

Three defects were found doing this, none of which was truncation:

**Inline XBRL was being ingested as prose.** Every filing hides ~19KB of
context and unit declarations in `<div style="display:none"><ix:header>`. The
parser was reading it as one 19,834-character "paragraph" — a single junk chunk
at the top of every document, embedded at full cost, matching nothing. The
parser now skips `display:none` subtrees and inline-XBRL headers.

**Oversized chunks.** A real 10-K has risk-factor paragraphs over 3,000
characters, and there was no ceiling. Chunks of 1,000+ tokens match broadly and
cite imprecisely. There is now a hard 600-token ceiling: paragraphs split on
sentence boundaries into parts keyed `p_abc#1`, `p_abc#2`, so a part still
resolves to its source paragraph. Tables split by row with the header repeated
on every part — a body row without its header is a list of numbers with no
meaning attached.

**Paragraph provenance was not stored.** The worker wrote the *chunk's* id into
`chunks.paragraph_id`, so the constituent paragraphs were never recorded and
"cite the paragraph" quietly degraded to "cite the chunk". Revision `0002`
separates `chunk_key` from `paragraph_ids`.

The ceiling took four attempts to hold, each failure found by measurement rather
than reading: budgets summed from parts under-counted the separators in the
joined text; the overlap carry re-inflated the next chunk; and a `hard` flush
still left a carry behind. `_chunk_section` now tracks characters including
separators and drops the carry across a ceiling flush, with an assertion on the
invariant.

## Chunk identity and provenance

### `chunk_hash`

Globally unique, derived from **company_id + document_accession + page_number +
normalised text**. Normalising first means a filing that is merely re-rendered
keeps its hashes.

Including the page is load-bearing, and here is the measurement that shows it —
taken from the real NVDA FY2025 10-K, 1,156 paragraphs:

| key | distinct | collisions |
| --- | --- | --- |
| with page | 1,155 | **1** |
| without page | 1,120 | 36 — `table of contents` alone appears **82 times** |

Without the page, every running header in the document collapses into a single
row. With it, exactly one genuine same-page duplicate collides. The ingest
worker drops that duplicate and reports the count rather than letting the
constraint abort a whole filing.

### Paragraph ids

`42_7` — page 42, paragraph 7. Readable in a citation, which is the point.

They are **positional**, so they move if a filing is re-paginated. That is a
deliberate trade: `chunk_hash` is the content-stable identity and is what
detects whether the words actually changed, so the pair covers both needs.
A paragraph split by the size ceiling keeps its parent: `42_7#1`, `42_7#2`.

### Provenance on every extracted object

`page_number`, `paragraph_id` and `confidence` are columns on `topic_mentions`,
`risks`, `people`, `metric_observations` and `timeline_events`. The API returns
them as one object, and it is **required** on the response model — a mention
cannot be serialised without saying where it came from:

```json
{
  "entity": "Export Controls",
  "provenance": {
    "chunk_hash": "2011b6f14d6da043…",
    "document_id": 1,
    "page": 14,
    "paragraph_id": "14_9",
    "confidence": 0.96
  }
}
```

`confidence` is the extractor's **own reported score** — a self-report, not a
calibrated probability. It is useful for ranking and triage thresholds, and
should not be shown to a reader as a likelihood that the claim is true. It is
nullable on purpose, so a missing score never reads as a confident zero.

## Known gaps

- **`HashingEmbedder` is still the default.** It matches vocabulary overlap, not
  meaning. In the smoke run it *did* rank the CapEx paragraph first for "why did
  capital spending on data centres increase" — but on shared words, and the
  scores (0.29 top hit) show it. Replace before this serves a real query.
- **Alembic autogenerate needed a hook.** It emits `pgvector.sqlalchemy.VECTOR`
  without importing pgvector, so the first revision failed with a `NameError`
  on a fresh database. `env.py` now has a `render_item` hook so later revisions
  get the import automatically. Worth knowing before adding a vector column.
- **Memory cards and promises** from the earlier layer are not in this chain
  yet. They become revision `0002` rather than a competing schema.
- The `ingest_worker` still writes through the older raw-SQL path; moving it
  onto these repositories is the next change.
