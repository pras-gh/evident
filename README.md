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
`brew install postgresql@17 pgvector`. Rendering filings to page images needs
Chrome, or `.venv/bin/playwright install chromium`.

```bash
tools/setup.sh      # .venv, web packages, a local database in .pgdata/, demo data, page images
tools/dev.sh api    # http://localhost:8000/docs  (one terminal)
tools/dev.sh web    # http://localhost:3000       (another)
```

`tools/setup.sh` is three steps you can also run alone:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
tools/db.sh up      # create, start and migrate PostgreSQL on :5434; writes .env
tools/db.sh seed    # three NVIDIA 10-Ks, keyword-extracted — no API key needed
```

For the same filings extracted by a model, through the AI gateway: about $0.12
on Gemini Flash-Lite, and free to repeat because it's cached. Seeding with
`tools/db.sh seed` afterwards puts the keyword demo back.

```bash
DATABASE_URL=... GEMINI_API_KEY=... .venv/bin/python tools/seed_demo.py \
  --corpus timeline --extract gateway --replace
```

The database is a private cluster in `.pgdata/` (git-ignored) on its own port;
nothing system-wide is installed or started. `tools/db.sh status|stop` manage
it. To use another PostgreSQL instead, put its URL in `.env` as
`DATABASE_URL` — see `.env.example` for every setting — and run
`cd db && ../.venv/bin/alembic upgrade head`. `tools/dev.sh api` checks the
database before starting and says what is wrong if it cannot serve from it.

Optional, per feature: `SEC_USER_AGENT` to ingest from EDGAR,
`GEMINI_API_KEY` / `GROQ_API_KEY` / `ANTHROPIC_API_KEY` for AI features (routed by
`config/ai.toml`; see `docs/ai-gateway.md`), and `EMBEDDING_PROVIDER` for search:
`gemini` uses the same `GEMINI_API_KEY`, or `voyage` / `openai` with their own
keys. There is deliberately no default embedding provider: `hashing` is for
tests only and has no semantics — it would make retrieval look like it works.
Search needs the chunks indexed once, and again after each ingest:
`python workers/embedding_worker.py` (the three demo 10-Ks are one request).

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
| `/admin/ai-usage` | AI spend, failures and limits — platform admins | `GET /v1/ai-usage/summary` |
| `/evidence/{chunkId}` | the filing's pages, at the cited paragraph | `GET /v1/evidence/{id}`, `POST /v1/evidence/resolve`, `GET /v1/document/{id}` + page images |

## Layers

| Layer | Stores | Answers |
| --- | --- | --- |
| substrate | what was *filed* — documents and chunks | "what does page 87 say" |
| memory | what we *know* — entities, mentions, relationships | "when did they first mention Blackwell" |
| graph | how it *connects* — importance and typed edges | "what drives what, and how much does it matter" |

**The invariant across all three: nothing enters memory without a paragraph that
asserts it.**

## Evidence viewer

`/evidence/{chunkId}?cite={chunk}:{paragraph}&entity={slug}` — the claim on the
left; on the right the filing opens at the page, scrolls to the paragraph and
highlights it, drawn over an image of the page. Filings are rendered to pages
by `workers/render_worker.py` (headless Chrome), which measures every
paragraph's box on the same pixels it photographs. See `docs/evidence-viewer.md`.

## AI gateway

Every AI request goes through `evident_ai.gateway`: business logic names a
task, and `config/ai.toml` routes it to Gemini, Groq or Claude, with fallback.
Every request is retried or rerouted on failure, capped by per-user and
per-organization limits, cached where it is filing analysis, costed, and logged
to `ai_usage_logs`. Platform admins see it all at `/admin/ai-usage`. Switching
a task to another provider is one config line or one `AI_ROUTE_*` variable.
See `docs/ai-gateway.md`.

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
