# Evident

Structured company intelligence from SEC filings. Every answer cites a page and
a paragraph.

**This is not a vector database.** The unit of value is the company, not the
chunk. See [docs/architecture.md](docs/architecture.md) for why that distinction
drives the whole design, and [docs/PRD.md](docs/PRD.md) for what the product is.

```
apps/
  web/          Next.js frontend
  api/          FastAPI backend
  marketing/    static landing site
packages/
  parser/       HTML + PDF parsing, chunking, stable paragraph ids
  memory/       CompanyMemory, cross-document resolution, memory cards
  retrieval/    embeddings, vector search, Postgres writes
  graph/        topic graph construction
  ai/           Claude prompts and entity extraction
workers/
  ingest_worker.py   fetch → parse → chunk → store
  memory_worker.py   extract → resolve → memory
  diff_worker.py     memory → card revisions
db/
  alembic/           migrations — the source of truth
  schema.sql         generated from the chain; apply to a fresh database
  legacy-design/     superseded hand-written SQL, NOT applied
docs/
  PRD.md  architecture.md  api.md  ingestion.md
```

## Running

Needs Python 3.11+, Node 18.18+, and **PostgreSQL 15+ with pgvector** — migration
0009 uses `NULLS NOT DISTINCT`, so 14 will not do. On macOS:
`brew install postgresql@17 pgvector`.

```bash
tools/setup.sh      # .venv, web packages, a local database in .pgdata/, demo data
tools/dev.sh api    # http://localhost:8000/docs  (one terminal)
tools/dev.sh web    # http://localhost:3000       (another)
```

`tools/setup.sh` is three steps you can also run alone:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
tools/db.sh up      # create, start and migrate PostgreSQL on :5434; writes .env
tools/db.sh seed    # three NVIDIA 10-Ks, keyword-extracted — no API key needed
```

The database is a private cluster in `.pgdata/` (git-ignored) on its own port;
nothing system-wide is installed or started. `tools/db.sh status|stop` manage
it. To use another PostgreSQL instead, put its URL in `.env` as
`DATABASE_URL` — see `.env.example` for every setting — and run
`cd db && ../.venv/bin/alembic upgrade head`. `tools/dev.sh api` checks the
database before starting and says what is wrong if it cannot serve from it.

Optional, per feature: `SEC_USER_AGENT` to ingest from EDGAR,
`ANTHROPIC_API_KEY` for live extraction with Claude, and `EMBEDDING_PROVIDER`
with a Voyage or OpenAI key for search. There is deliberately no default
embedding provider: `hashing` is for tests only and has no semantics — it would
make retrieval look like it works.

```bash
# tests — the database suites run when TEST_DATABASE_URL points at an empty,
# disposable database; they drop and recreate its tables
PYTHONPATH=packages/db:packages/parser:packages/memory:packages/retrieval:packages/graph:packages/ai:apps:.:tests \
  .venv/bin/python -m unittest discover -s tests
cd apps/web && npm test
```

| Page | What it shows | Backed by |
| --- | --- | --- |
| `/` | companies with memory | `GET /v1/companies` |
| `/memory/{ticker}` | memory cards and recent changes | `GET /v1/companies/{t}`, `/cards`, `/cards/{kind}`, `/v1/company/{t}/timeline` |
| `/timeline/{ticker}` | what changed between filings | `GET /v1/company/{t}/timeline` |
| `/evidence/{ticker}/{entity}` | the filing, at the cited paragraph | `GET /v1/companies/{t}/entities/{slug}`, `POST /v1/evidence/resolve`, `GET /v1/documents/{id}/pages` |

## Layers

| Layer | Stores | Answers |
| --- | --- | --- |
| substrate | what was *filed* — documents and chunks | "what does page 87 say" |
| memory | what we *know* — entities, mentions, relationships | "when did they first mention Blackwell" |
| graph | how it *connects* — importance and typed edges | "what drives what, and how much does it matter" |

**The invariant across all three: nothing enters memory without a paragraph that
asserts it.**

## Evidence viewer

`/evidence/{ticker}/{entity}` — a claim on the left, the filing on the right;
clicking a citation scrolls to its page and holds the paragraph highlighted.
To run it without an API key, see `docs/evidence-viewer.md` → *Running it
locally*.

## Timeline

`/timeline/{ticker}`, over `GET /v1/company/{ticker}/timeline` — what changed
between a company's filings: newly disclosed, expanded, narrowed and dropped
topics, each linked to the paragraph that shows the change. Events come from
comparing each filing with the previous one of the same form, never from
`first_seen`, so a single filing produces no false "new" events. See
`docs/timeline-engine.md`, including how the thresholds were chosen.

```bash
# three real NVIDIA 10-Ks, keyword-extracted — no API key needed
DATABASE_URL=... python tools/seed_demo.py --corpus timeline
open http://localhost:3000/timeline/nvda
```
