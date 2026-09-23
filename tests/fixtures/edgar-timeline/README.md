# Timeline corpus

Three consecutive NVIDIA Form 10-K filings, so the timeline engine has real
year-over-year change to find. SEC filings are public domain.

| Fiscal year | Accession              | Filed      | Item 1A pages | Paragraphs |
|-------------|------------------------|------------|---------------|------------|
| FY2024      | `0001045810-24-000029` | 2024-02-21 | 13–29         | 42         |
| FY2025      | `0001045810-25-000023` | 2025-02-26 | 13–31         | 58         |
| FY2026      | `0001045810-26-000021` | 2026-02-25 | 13–30         | 62         |

**What is real.** Every paragraph is verbatim from Item 1A, Risk Factors, of
the filing named, read from `www.sec.gov`, and sits on the page it is on in the
filing — the HTML replays the filing's page breaks, so the parser counts pages
the way it would on the original. Accession numbers, form types, filing dates,
period-of-report dates and acceptance times are copied from each filing's
EDGAR index. The rest of each filing (Items 1B–16, the financial statements)
is omitted, but its page breaks are kept, so a document here has the same page
count as the original — 85, 87 and 85. The acceptance times are published in Eastern time; they are
stored here converted to UTC, which is how the ingest worker reads them.

**What is filtered.** Only paragraphs that name something in the demo
vocabulary (`tools/seed_demo.py`) are kept, so a filing here is a subset of its
Risk Factors, not the whole section. Counts across years are comparable because
the same filter was applied to all three.

**Why this matters for the timeline.** The engine compares how many paragraphs
of each filing discuss a topic. Filtering removes paragraphs that name none of
the vocabulary, so it cannot change a count for a vocabulary term — but it does
mean the corpus says nothing about topics outside it.

Do not edit paragraphs in place. The timeline tests assert on counts derived
from this text; add a filing rather than changing one.
