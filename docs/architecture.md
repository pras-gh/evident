# Architecture

## The shape of the problem

The product promise is that every answer cites a page and a paragraph. That is
an **ingestion** guarantee, not a retrieval one: if provenance is lost while
parsing, no retrieval strategy recovers it. Everything below is arranged around
not losing it.

The second constraint is that the unit of value is the **company**, not the
document and not the chunk. "When did they first mention Blackwell", "who has
run this segment", "what did they promise in 2024 and did they deliver" are not
similarity questions. They need resolved entities with a time axis.

## Three layers

```
   filings                 ┌─────────────────────────────────────────┐
      │                    │ 3. cards      what a person reads       │
      ▼                    │    revisions, diffs, "updates from"     │
┌───────────┐              ├─────────────────────────────────────────┤
│  parser   │─────────────▶│ 2. memory     what we know              │
│ html/pdf  │   blocks     │    topics people metrics risks          │
└───────────┘   tables     │    promises products events timeline    │
                sections   ├─────────────────────────────────────────┤
                           │ 1. substrate  what was filed            │
                           │    documents sections blocks tables     │
                           │    chunks + embeddings (an index)       │
                           └─────────────────────────────────────────┘
```

**Layer 1 — substrate.** Documents, sections, paragraphs, tables. Chunks and
embeddings live here as an *index over evidence*, not as the primary model.
Answers "what does page 87 say".

**Layer 2 — memory.** Resolved entities accumulated across every filing a
company has made. Answers "when did this start".

**Layer 3 — cards.** Derived projections with history. Answers "what changed".

**The invariant across all three: nothing enters memory without a paragraph that
asserts it.** Every typed row references `evidence`, which points at a document,
a page and a paragraph id.

## Repository layout

```
apps/
  web/          Next.js frontend
  api/          FastAPI backend, read-only
  marketing/    the static landing site
packages/
  parser/       HTML + PDF parsing, chunking, stable paragraph ids
  memory/       CompanyMemory, cross-document resolution, memory cards
  retrieval/    embeddings, vector search, Postgres writes
  graph/        topic graph construction
  ai/           Claude prompts and entity extraction
workers/
  ingest_worker.py    fetch → parse → chunk → store
  memory_worker.py    extract → resolve → memory
  diff_worker.py      memory → card revisions
db/
  schema.sql          generated; apply to a fresh database
  migrations/         apply individually to an existing one
```

`apps/marketing/` is not in the original layout sketch. The landing site is a
static pixel-art build with its own generators, and forcing it through Next.js
would be a rewrite rather than a move — so it sits beside `web/` until someone
decides otherwise.

## Why the packages split this way

The split follows **what changes together**, not what sounds tidy.

- `parser` changes when a filing format changes. It knows nothing about
  companies, only about documents.
- `memory` changes when the entity model changes. It has no I/O at all, which
  is why resolution and the card lifecycle are unit-testable with nothing
  installed.
- `ai` changes when a prompt changes — and prompts are versioned, because a
  changed prompt changes extractions. Storing the version alongside every
  extraction is what separates "the company restated" from "we reworded an
  instruction".
- `retrieval` and `graph` are both read-side derivations that can be rebuilt
  from layers 1 and 2 without re-fetching anything.

The dependency direction is strictly one way: `ai` and `retrieval` depend on
`parser` and `memory`; nothing depends on the workers or the apps.

## Workers

Three stages, each independently runnable, so a bad parse never forces a
re-fetch and a re-embed never forces a re-parse.

| Worker | Reads | Writes | Idempotent on |
| --- | --- | --- | --- |
| `ingest_worker` | EDGAR | documents, sections, blocks, tables, chunks | accession + content digest |
| `memory_worker` | blocks | topics, people, metrics, risks, promises, products, timeline | normalised entity identity |
| `diff_worker` | memory | card_revisions | (card, document) |

Re-running any of them is safe. That is not incidental: filings get amended,
extractions get re-run after a prompt change, and a pipeline that duplicates on
retry is a pipeline nobody dares retry.

## Two decisions that constrain everything downstream

**Paragraph ids are content-addressed, not counters.** Re-ingesting an unchanged
filing yields identical ids, so a citation issued months ago still resolves.
Re-ingesting a *corrected* filing yields new ids only for the paragraphs that
changed — so a diff is free, and stale citations fail loudly rather than
silently pointing at different words.

**Promises are never marked `broken` without evidence.** A company going quiet
about a commitment is suggestive, not probative. Silence past the horizon
becomes `unclear`, which is surfaced — an unresolved promise past its due date
is itself the finding. Asserting failure from absence would be the same error as
inventing a filing quote.

## What is deliberately not here

- **No vector database.** pgvector sits inside Postgres because the vectors are
  an index over rows that already exist; a separate store would mean keeping two
  systems in sync for no gain at this size.
- **No queue yet.** The workers are callable functions. When ingestion volume
  justifies it they become tasks; the interfaces already assume that by taking
  explicit inputs rather than reaching for global state.
