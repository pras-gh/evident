# elevate-ingest

Automated ingestion for SEC filings. Takes an EDGAR accession number, produces
structured, citable entities in PostgreSQL.

The product promise is "every answer cites a page and a paragraph." That is an
**ingestion** guarantee, not a retrieval one — if provenance is lost while
parsing, no amount of clever retrieval gets it back. Everything below is
organised around not losing it.

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

| Requirement | How |
| --- | --- |
| Page numbers | PDF: real page index. HTML: SEC page-break markers, carried as a running counter |
| Section titles | Item headings detected structurally, kept as a path (`Part II › Item 7 › Capital Expenditures`) |
| Tables | Extracted to their own entity with cells intact — never flattened into prose chunks |
| Paragraph IDs | Deterministic and content-addressed, so the same input yields the same ID on every run |
| Document metadata | CIK, ticker, form type, accession, fiscal period, source URL |
| Publication timestamp | EDGAR acceptance datetime (when it hit the wire), not the fetch time |

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
