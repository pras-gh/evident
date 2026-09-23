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
