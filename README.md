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
  schema.sql         apply to a fresh database
  migrations/        apply individually to an existing one
docs/
  PRD.md  architecture.md  api.md  ingestion.md
```

## Running

```bash
# database — needs pgvector for chunk_embeddings; the other 27 tables do not
psql "$DATABASE_URL" -f db/schema.sql

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
| substrate | what was *filed* — documents, sections, paragraphs, tables | "what does page 87 say" |
| memory | what we *know* — resolved entities with a time axis | "when did they first mention Blackwell" |
| cards | what a person *reads* — projections with history | "what changed, and when" |

**The invariant across all three: nothing enters memory without a paragraph that
asserts it.**
