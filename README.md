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

```bash
# database — needs pgvector
createdb evident && psql evident -c 'create extension vector'
export DATABASE_URL=postgresql+psycopg://localhost/evident
cd db && alembic upgrade head        # or: psql "$DATABASE_URL" -f db/schema.sql

# tests: the core is standard-library only and runs on a clean checkout
python3 -m unittest discover -s tests

# api
cd apps/api && uv sync && uvicorn main:app --reload

# web
cd apps/web && npm install && npm run dev
```

## Layers

| Layer | Stores | Answers |
| --- | --- | --- |
| substrate | what was *filed* — documents and chunks | "what does page 87 say" |
| memory | what we *know* — entities, mentions, relationships | "when did they first mention Blackwell" |
| graph | how it *connects* — importance and typed edges | "what drives what, and how much does it matter" |

**The invariant across all three: nothing enters memory without a paragraph that
asserts it.**
