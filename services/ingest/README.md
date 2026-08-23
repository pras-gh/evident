# elevate-ingest

Automated ingestion for SEC filings. Takes an EDGAR accession number, produces
structured, citable entities in PostgreSQL.

**This is not a vector database.** It is structured intelligence.

The unit of value is the *company*, not the chunk. Two layers:

**Layer 1 — substrate** (`sql/001_schema.sql`) stores what was filed: documents,
sections, paragraphs, tables. Chunks and embeddings live here too, as an index
over evidence. This layer answers *"what does page 87 say."*

**Layer 2 — memory** (`sql/002_company_memory.sql`) stores what we know: resolved,
typed entities with a time axis, accumulated across every filing a company has
ever made.

```
CompanyMemory {
  companyId, ticker,
  documents[], timeline[], topics[], people[],
  metrics[], risks[], promises[], products[], events[]
}
```

This layer answers *"when did they first mention Blackwell"*, *"who has run this
segment"*, *"what did they promise in 2024 and did they deliver."* No amount of
similarity search over layer 1 produces those — they need resolved entities and
dates.

**Layer 3 — cards** (`sql/003_memory_cards.sql`) is the read model. Cards are what
a person reads; entities are what the system stores.

| Memory Card | Updates From |
| --- | --- |
| Revenue | 10-Q / 10-K |
| Products | Earnings Call |
| Guidance | CEO statements |
| Risks | Risk section |
| CapEx | Cash Flow section |
| Litigation | Legal section |

The invariant across all three layers: **nothing enters memory without a
paragraph that asserts it.** Every typed row references `evidence`, which points
at a document, a page and a paragraph id.

## Status

Work in progress on `feat/ingestion-service`. See the PR for what has landed.

## Pipeline

```
EDGAR  ──►  fetch  ──►  parse  ──►  chunk  ──►  embed  ──►  PostgreSQL
                        HTML|PDF     +IDs      pluggable
```

Each stage is independently runnable so a bad parse can be re-run without
re-fetching, and a re-embed does not require a re-parse.

## What gets preserved

### Layer 1 — what gets preserved

| Requirement | How |
| --- | --- |
| Page numbers | PDF: real page index. HTML: SEC page-break markers, carried as a running counter |
| Section titles | Item headings detected structurally, kept as a path (`Part II › Item 7 › Capital Expenditures`) |
| Tables | Extracted to their own entity with cells intact — never flattened into prose chunks |
| Paragraph IDs | Deterministic and content-addressed, so the same input yields the same ID on every run |
| Document metadata | CIK, ticker, form type, accession, fiscal period, source URL |
| Publication timestamp | EDGAR acceptance datetime (when it hit the wire), not the fetch time |

### Layer 2 — what gets resolved

| Entity | What resolution buys |
| --- | --- |
| `topics[]` | One topic across every document that discusses it, with first/last seen |
| `people[]` | One person across spelling variants, with **dated roles** — "the CFO" means someone different in 2019 |
| `metrics[]` | One series across label drift (`CapEx` = `Capital Expenditures`); a revised figure is flagged as a restatement, not deduplicated |
| `risks[]` | A risk that stops being disclosed is marked `dropped`, not deleted — the disappearance is the signal |
| `promises[]` | Forward-looking commitments carried until something settles them |
| `products[]` | Lifecycle from first mention through shipping |
| `events[]` | Discrete dated occurrences |
| `timeline[]` | A materialised spine over all of the above — one indexed read, not a nine-way union |

## On cards

A card is **not a current value**. It is an append-only series of revisions —
one per filing that touched it, each carrying a diff against the one before.

```
CapEx  ── Updates from: Cash Flow section        4 revisions, 3 material
  ● rev 1  2023-02-01  FY2023 10-K   1 new item: CapEx.
  ● rev 2  2024-02-01  FY2024 10-K   CapEx rose from 8,100 to 10,922.
  ○ rev 3  2024-08-01  Q3 10-Q       Restated without change.
  ● rev 4  2025-02-01  FY2025 10-K   CapEx rose from 10,922 to 14,602.
```

`"CapEx: $14.6B"` is a number. The block above is a card, and it only exists
because revision 1 was kept.

Three properties the implementation holds:

- **Append-only.** Nothing is updated in place — an in-place update would
  destroy the history that is the point.
- **Idempotent per filing.** Re-ingesting a document does not add a second
  revision. Duplicating history is worse than missing an update.
- **Materiality is tracked.** A filing that touches a card without moving
  anything is recorded but marked immaterial, so the UI can say "4 updates,
  3 material" rather than implying every filing mattered.

### Routing binds at three granularities

The six bindings are not the same kind of rule, which is why routing is a
predicate over `(document, section, speaker)` rather than a lookup:

| Card | Binds on | Rule |
| --- | --- | --- |
| Revenue | form type | `10-K`, `10-Q` |
| Products | document kind | `earnings_call` |
| Guidance | **speaker role** | `CEO` |
| Risks | section | `item 1a\|risk factors` |
| CapEx | section | `cash flow\|capital expend` |
| Litigation | section | `item 3\|legal proceedings` |

A `NULL` predicate means "don't care", so one filing can update several cards —
a 10-K's Item 1A updates both Revenue and Risks. Rules live in `card_sources`
as **data, not code**, so adding "Segment Margins ← MD&A" is an insert. A test
asserts the Python defaults and the SQL seed agree, because a silent drift means
a filing quietly stops updating a card.

### What the diff surfaces

The most valuable line a card produces is usually a *removal*:

```
Risks  ── Updates from: Risk section
  ● rev 3  2025-02-01  FY2025 10-K   1 no longer disclosed: China export controls.
```

Guidance is worth noting too: it reads `promises[]`. Guidance **is** a promise —
same entity, read differently — so a guidance card inherits the promise
lifecycle for free, including the refusal to call anything `broken` without
evidence.

## On promises

The entity a vector database cannot represent. A forward-looking statement is
not a fact; it is an open obligation with a lifecycle.

`resolve.py` will **never mark a promise `broken` on its own.** A company going
quiet about a commitment is suggestive, not probative, and asserting failure
without evidence would be the same error as inventing a filing quote. Silence
past the horizon becomes `unclear` — and an unresolved promise past its due
date is itself the finding, surfaced by `overdue_promises()`. `broken` requires
a resolution signal that carries evidence.

## On extraction

Turning prose into typed entities is the one place a language model belongs.
The model receives paragraphs that already carry ids and must cite one for
every entity it returns. Anything citing an id we did not supply is **dropped,
not stored** (`extract.drop_uncited`), and the drop count is reported — a rising
drop rate is the signal that a prompt or model change has started inventing
citations. Without that check, "every answer is backed by evidence" is a slogan.

## On embeddings

There is no embeddings API in the Anthropic SDK, so this service does not hard-code
a provider. `embed.py` defines an `Embedder` protocol; every stored vector carries
its `provider`, `model` and `dim` alongside it, so you can re-embed, run two
providers side by side, or migrate without guessing what produced a given row.

A dependency-free `HashingEmbedder` ships as the default so the whole pipeline runs
and is testable with no credentials. It is **not** semantically useful — swap in a
real provider before this serves anything.

## Running

```bash
pip install -r requirements.txt
psql "$DATABASE_URL" -f sql/001_schema.sql      # needs pgvector on the server
export SEC_USER_AGENT="Elevate ingest (you@example.com)"   # SEC refuses anonymous traffic

python -m elevate_ingest.cli ingest --cik 320193 --accession 0000320193-25-000073
```

Parse a local file with no network and no database — useful for checking a
parser change against a filing you already have:

```bash
python -m elevate_ingest.cli parse --file filing.htm --accession 0000320193-25-000073
```

## Tests

The core is dependency-free, so the suite runs with nothing installed:

```bash
python3 -m unittest discover -s tests
```

22 tests cover paragraph-id stability, section/page/table extraction, the
chunker's two invariants, and embedding determinism. The EDGAR, PDF and
Postgres paths are excluded on purpose — they are thin adapters over
third-party libraries, and the logic worth protecting is not in them.

## Known gaps

- **PDF tables** return `[]`. Extracting cells from a PDF needs layout
  analysis that `pypdf` does not do; returning nothing is honest, returning
  garbled cells would not be. Wire in `pdfplumber` or Camelot when the PDF
  path matters.
- **`HashingEmbedder` carries no semantics.** It exists so the pipeline runs
  without credentials. Replace it before serving a real query.
- **Section detection is heuristic**, tuned for `Part`/`Item` headings in
  10-K/10-Q. Other form types will produce a flatter section tree.
