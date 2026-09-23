# Timeline Engine

`GET /v1/company/{ticker}/timeline` — what changed in a company's filings, and
when, each event carrying the paragraph that shows it.

| field | example |
|---|---|
| date | Feb 2025 |
| category | Risk |
| title | Export Controls expanded |
| evidence | 10-K · page 27 → opens the paragraph in the evidence viewer |

## The trap in "the backend already stores dates"

It does — every mention carries the date of its filing, and every entity has a
`first_seen`. The tempting timeline is one event per entity at `first_seen`:
*"Export Controls first appears, Feb 2025."* The previous timeline builder did
exactly that.

It is false. `first_seen` is the first filing **Evident ingested** that mentions
the entity, not the first filing the company wrote about it. NVIDIA has disclosed
export controls since 2022; with one filing loaded, every entity in it would
"first appear" on the same day, and the timeline would be a list of confident
mistakes.

So events come from **comparing filings with each other**, never from dates alone.

## Events

Each filing is compared with the previous filing **of the same form** — a 10-K
with the last 10-K. A 10-Q's risk factors usually only describe changes since
the 10-K, so comparing across forms would report half the risk factors as
dropped every quarter.

| event | when | evidence |
|---|---|---|
| **newly disclosed** | named in this filing and in no earlier one | its first paragraph here |
| **expanded** | cited in ≥2 more paragraphs, and ≥1.5× as many, as last time | its first paragraph here |
| **narrowed** | the reverse | its first paragraph here |
| **no longer disclosed** | annual filings only: in the last one, absent from this | where it last appeared |
| **filed** | every filing | the filing |

**The earliest filing of each form is a baseline.** Nothing in it is marked new,
because there is nothing to have been new relative to. The response says which
filings it covers, so the UI can say so too.

Paragraph counts are meaningful because mentions are one per paragraph (0009);
under the old one-per-chunk key, "cited in 7 paragraphs" would have been "cited
in 3 chunks", which says more about the chunker than the company.

## Computed on read

Events are derived from `entity_mentions` and `documents` at request time, not
stored. That is what makes the feature nearly free: no second copy of the truth
to keep in sync, no backfill when extraction improves, and a re-extracted filing
changes the timeline the moment it lands. The existing `timeline_events` table
stays what it is — a raw event log, served at `/v1/companies/{ticker}/timeline`.
