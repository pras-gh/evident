# Evidence Viewer

Every claim links to the paragraph that supports it. Click a citation and the
document scrolls to the page and holds that paragraph highlighted.

```
┌───────────────────────────┬──────────────────────────────────────┐
│ answer                    │ 10-K · 0001045810-25-000023   p.27/87 │
│                           │ ┌──────────────────────────────────┐ │
│ Export controls restrict  │ │ page 27                          │ │
│ Blackwell shipments to    │ │  …                               │ │
│ China.                    │ │ ▌In October 2023, the USG        │ │
│                           │ │ ▌announced new licensing …       │ │
│ [1 10-K p.27] [2 10-K p.28]│ │  …                               │ │
└───────────────────────────┴──────────────────────────────────────┘
```

## Three places the spec and the data disagree

**The filings are HTML, not PDF.** A 10-K's primary document is
`nvda-20250126.htm`. There is no PDF to hand a PDF viewer, and the raw filing is
not stored at all — only its URL and hash. So the document pane renders the
filing as a paged reading view built from what *is* stored: chunks, split back
into their paragraphs, grouped by page. Pages look and behave like pages, and
every paragraph is an addressable element.

This is also the more precise option. A PDF highlight is a rectangle drawn over
glyphs at coordinates; an HTML highlight is the paragraph element itself, which
reflows correctly at any width and cannot drift off its text.

**There are no bounding boxes.** Neither parser records coordinates — the HTML
path has none to record, and the PDF path uses `extract_text()`. The API returns
`bounding_box: null` rather than inventing one, and documents what populating it
would need: storing raw PDFs and extracting word coordinates at parse time. The
field is in the contract so a PDF source can fill it without a breaking change.

**There are no AI answers yet.** Nothing in the API generates an answer; `/v1/search`
returns chunks. The viewer renders an `Answer` — text plus citations — and does
not care where it came from. Today the one source of claims-with-evidence is the
entity graph, so the viewer page shows an entity as the claim and its mentions
as the citations. When an answer endpoint exists it feeds the same component.

## Anchors

Every citable unit has an anchor:

| unit | anchor | highlight |
|---|---|---|
| prose paragraph | `p-{paragraph_id}` | the paragraph |
| table chunk (no paragraph ids) | `c-{chunk_id}` | the table |

A citation that names a paragraph highlights that paragraph. A citation that
names only a chunk highlights every paragraph in it.

## Pages come from the paragraph, not the chunk

A chunk can span pages — in the benchmark corpus every chunk does — but the
database stores one `page_number` per chunk, its first. Resolving a citation to
the chunk's page would scroll to the wrong page for any paragraph after the
first break. The paragraph id carries its own page (`27_3` is page 27,
paragraph 3), so that is what resolution uses.

## Found while building this: mentions cited the wrong paragraph

Extraction showed Claude each chunk as one block, labelled with the chunk's
*first* paragraph id. Claude could only ever cite that id, so every entity
mention recorded the chunk's first paragraph and first page — even when the
entity was in paragraph four, two pages later. A viewer built on that would
highlight the wrong paragraph for most citations.

Extraction now shows each chunk as its individual numbered paragraphs, still in
one request per chunk. Claude cites the paragraph that actually supports the
entity, and the mention records that paragraph and its page.

## API

| | |
|---|---|
| `GET /v1/evidence/{chunk_id}` | one citation: page, paragraph, document, confidence, bounding box |
| `POST /v1/evidence/resolve` | many citations in one call, order preserved, unresolvable ones flagged rather than failing the answer |
| `GET /v1/documents/{id}/pages` | the paged reading view the viewer renders |

## Running it locally

```bash
echo 'DATABASE_URL=postgresql+psycopg://localhost/evident' > .env   # git-ignored
(cd db && alembic upgrade head)
DATABASE_URL=... python tools/seed_demo.py     # real filing text, keyword-matched entities
tools/dev.sh api                               # :8000
tools/dev.sh web                               # :3000
open http://localhost:3000/evidence/nvda/export_controls
```

`seed_demo.py` is **not Claude** — it finds a fixed vocabulary of names by
keyword, so the viewer can be run without an API key. Every citation it writes
still points at a paragraph that really contains the name, through the real
extraction path, Pydantic gate and citation guard.

`.env` is read as `KEY=value` data rather than sourced as shell, because
connection strings carry `&`, and sourcing reads `...host=/tmp&port=5433` as a
background job and leaves `DATABASE_URL` unset.

## Verified

**Automated** — 237 Python tests and 19 frontend tests pass, and `next build`
succeeds. The evidence suite runs against the synthetic 10-K because it has
both hard cases: 53 chunks that span a page break and 56 tables. Its central
test resolves every one of the 474 chunks and finds every resulting anchor in
the reading view, on the page the citation names.

**In a browser**, against the seeded Risk Factors corpus:

- all seven Export Controls citations land on their own page, the paragraph
  96px below the top of the pane, fully visible, exactly one paragraph lit
- the highlight survives real mouse-wheel scrolling eight pages away and back;
  the page indicator follows the scroll and the highlight does not move
- one highlight at a time, the pressed chip follows the click
- stacked layout at 375px, no horizontal scroll

The frontend tests run in jsdom, which has no layout, so they prove what the
viewer *does* — which element it scrolls to, what it highlights, that a scroll
never clears the highlight. Only the browser proves it *works*, and it caught
the one failure the tests could not.

## Found while building this

**Mentions cited the wrong paragraph.** Described above: extraction labelled
each chunk with its first paragraph id. On the seeded corpus, 25 of 26 mentions
now cite a later paragraph than the chunk's first, and 19 of 26 sit on a
different page from the chunk's stored one — every one of those would have
highlighted the wrong text, and 19 would have scrolled to the wrong page.

**Mentions were unique per chunk.** `entity_mentions` was unique on
`(entity_id, chunk_id)`, which was harmless while every citation in a chunk
named the same first paragraph. Once citations were precise, the first
paragraph cited in a chunk won and the rest were silently dropped: export
controls is named in ten paragraphs of the corpus and four were stored, a
subsection heading among them, while the paragraphs describing the USG
licensing rules were discarded. Revision `0009` keys mentions on
`(entity_id, chunk_id, paragraph_id)` with `NULLS NOT DISTINCT`, so a rerun
still cannot inflate a count. Seven citations now, all substantive.

**The document pane could not scroll.** The grid row sized to its content, so
the pane grew to the full height of the filing — 11,742px — instead of
scrolling, and the shell clipped the overflow. A reader could not scroll the
document at all, and `scrollTo` had nothing to move. All 19 jsdom tests passed
through this. Fixed by pinning the row with `minmax(0,1fr)` and giving every
pane `min-height:0`.

**The page indicator depended on callback timing.** Its scroll throttle
assumed `requestAnimationFrame` always calls back after returning. Real
browsers do, so production was unaffected, but code that silently depends on
that is fragile; it now uses a flag instead.

## Not done

- **No PDF rendering and no bounding boxes** — see the top of this document.
- **No AI answers.** The viewer is ready for them; nothing produces them.
- **Existing `mention_count` values** were counting chunks, not paragraphs.
  `0009` does not recompute them; re-extracting a document corrects its counts.

## Already broken, not touched here

- `/` redirects to `/memory/AAPL`, whose page calls `/v1/companies/{t}` and
  `/v1/companies/{t}/cards`. Neither endpoint exists — the memory-card layer
  was never ported to the current schema — so the app's home page returns 500.
- `npm audit` reports advisories in `next`, `postcss` and `sharp`, all
  pre-existing dependencies. Fixing them means a Next.js upgrade, which belongs
  in its own change.
