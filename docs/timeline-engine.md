# Timeline Engine

`GET /v1/company/{ticker}/timeline` — what changed in a company's filings, and
when, each event carrying the paragraph that shows it. Rendered at
`/timeline/{ticker}`.

| field | example |
|---|---|
| date | Feb 2026 |
| category | Risk |
| title | Export Controls Expanded |
| evidence | 10-K · p. 27 → opens that paragraph in the evidence viewer |

## The trap in "the backend already stores dates"

It does — every mention carries the date of its filing, and every entity has a
`first_seen`. The tempting timeline is one event per entity at `first_seen`:
*"Export Controls first appears, Feb 2025."* The previous timeline builder did
exactly that.

It is false. `first_seen` is the first filing **Parallx ingested** that mentions
the entity, not the first filing the company wrote about it. NVIDIA has disclosed
export controls since 2022; with one filing loaded, every entity in it would
"first appear" on the same day, and the timeline would be a list of confident
mistakes.

So events come from **comparing filings with each other**, never from dates
alone. `tests/test_timeline_e2e.py::OneFiling` ingests one real 10-K with twelve
entities in it and asserts the timeline says nothing about any of them.

## Events

Each filing is compared with the previous filing **of the same form** — a 10-K
with the last 10-K. A 10-Q's risk factors usually only describe changes since
the 10-K, so comparing across forms would report half the risk factors as
dropped every quarter.

| kind | when | evidence |
|---|---|---|
| **newly disclosed** | cited here, and in no earlier filing of this form | first mostly-new paragraph here |
| **expanded** | cited in ≥3 more paragraphs than last time, and ≥1.25× as many | first mostly-new paragraph here |
| **narrowed** | the mirror image, and still cited | first mostly-new paragraph here |
| **no longer disclosed** | annual forms only: cited last time, not this time | where it last appeared |
| **disclosed again** | cited here, absent last time, cited in an older filing of this form | first mostly-new paragraph here |
| **filed** | every filing | — (links to the filing on EDGAR) |

**The earliest filing of each form is a baseline.** Nothing in it is marked new,
because there is nothing to have been new relative to. The response lists every
filing with how it was used — `baseline`, `compared` or `not compared` — and the
page says so above the timeline, so a short timeline reads as "not enough
history" rather than "nothing happened".

**Amendments and non-periodic forms are listed but never compared.** A 10-K/A
often restates one Part of the report; compared with a complete 10-K it would
report most of the report as dropped. An 8-K is about one event, so its not
mentioning China means nothing. Periodic forms are 10-K, 10-Q, 20-F and 40-F;
"no longer disclosed" is limited to the annual ones.

**A topic first named in a 10-Q** and later in a 10-K is still *newly disclosed*
in the 10-K — it is new to the annual report — but the summary says it was named
earlier in a 10-Q.

### Counting paragraphs

The measure is paragraphs citing the topic, not mentions. Mentions are one per
entity per paragraph since 0009; under the old one-per-chunk key, "cited in 7
paragraphs" would have been "cited in 3 chunks", which says more about the
chunker than the company. Two more rules keep the count about the company:

- **Base ids.** An oversized paragraph the chunker split on sentence boundaries
  (`25_1`, `25_1#2`) is one paragraph.
- **Distinct per document.** Consecutive chunks overlap by a paragraph, and each
  records its own mention; the paragraph counts once.

A paragraph that runs over a page break still counts twice — the parser sees
two blocks — which is one reason small differences are not reported.

### Thresholds, and how they were chosen

Expanded needs **both** at least 3 more paragraphs **and** at least 1.25× as
many. Either alone misfires: 1 → 2 doubles on one paragraph, and 40 → 43 is
three more but noise at that size.

**These were set after looking at the data**, three years of NVIDIA Risk Factors
(below), not derived from first principles. The first draft of this document
said ≥2 and ≥1.5×. Measured, a floor of 2 reports Israel going 4 → 6 and back
to 4, movement the size of what re-paginating or splitting a bullet does on its
own; and 1.5× misses China's 13 → 17 (1.31×) and FY2026's export controls
20 → 26 (1.3×). They will need revisiting against filers who write differently; both are
constants in `parallx_graph/timeline.py` and reported in every response under
`thresholds`.

### Which paragraph is the evidence

The first paragraph citing the topic is usually the wrong one to show. For
FY2026 export controls it is a bullet carried over from FY2025 with one word
dropped; pointing there shows nothing about why the count went from 20 to 26.

So the evidence is the first paragraph, in reading order, that is **mostly new**:
under half of its four-word phrases appear anywhere in what the compared filing
said about the same topic. Two simpler tests fail on these filings:

- **Exact text** — NVIDIA lightly edits nearly every paragraph each year; only 2
  of FY2026's 26 export-control paragraphs are verbatim from FY2025, so almost
  everything would read as new.
- **Paragraph-to-paragraph similarity** — page breaks cut paragraphs at
  different points each year. The page-25 half of a FY2025 paragraph is a small
  part of the whole FY2024 paragraph it came from, scores low against it, and
  reads as new when every word of it was there last year.

Phrase overlap against *everything* the old filing said on the topic handles
both, and is sharply two-sided on this corpus: of the 94 paragraphs scored in
the six expanded and narrowed comparisons, 25 score under 0.2, 65 score 0.5 or
more, and four score 0.35–0.43. Nothing scores between 0.43 and 0.56, so the 0.5
cutoff sits in a gap rather than in the data. (A newly disclosed topic has no
earlier text, so all its paragraphs score 0; they are left out of those
figures.) Like the thresholds, the cutoff was chosen after looking.

"Mostly new" is measured against paragraphs that cited the *same topic*: a
paragraph that existed last year but did not mention tariffs, and now does, is
new material about tariffs. That is how FY2025's first tariffs paragraph — a
bullet that gained "or tariffs" — is correctly the evidence for Tariffs
Expanded.

Each evidence object says whether it was chosen this way (`new_paragraph`), so
the UI marks it "new this filing" only when that is true.

### Checked against the text

Mentions record what extraction found, not what the filing says. A model names
a topic differently from year to year, or misses it, and each slip reads as a
change: dropped under one name, new under another. So "newly disclosed",
"disclosed again" and "no longer disclosed" are checked against the full text
of the compared filing. The event is **withheld** when a sentence quoted for
the topic is in both filings. For a new topic that means this year's quote
was already in last year's filing. For a drop, it means last year's quote is
still in this year's.

"In both" is the carried-over test applied to a quote: at least half its
four-word phrases are in the other filing (`CARRIED_OVER`). NVIDIA edits most
sentences a little each year, and sometimes the edit *is* the disclosure, so
two more conditions must hold:

- **The edit didn't add or remove the topic's name.** FY2025 kept a FY2024
  sentence and added "by the January 2025 AI Diffusion IFR": 73% carried over,
  but the new words are the topic. A name word counts as added or removed when
  it is outside the shared phrases and neither neighbour pair ("to china") is
  in the other filing either. A word that merely sits beside an edit keeps its
  pair; one that was inserted or cut does not.
- **No new clause.** Four or more new words in a row are a new clause. It may
  name the topic in other words than its name, so the event stands.

Expanded and narrowed are not checked this way. They compare counts of
paragraphs present in both filings, which the text doesn't contradict.

Measured on the demo 10-Ks:

| extraction | change events | withheld | the 10 keyword events |
|---|---|---|---|
| keyword matcher | 10 | 0 | all kept |
| Gemini 3.5 Flash-Lite, prompt v3.1 | 128 → 61 | 67 (32 drops, 31 new, 4 returning) | — |
| Gemini 3.5 Flash-Lite, prompt v4.2 | 41 → 38 | 3 | — |

Prompt v4.2 reuses known topic names (`docs/real-entity-extraction.md`), so
drift mostly doesn't happen in the first place, and the text check has little
left to withhold.

A sample of the withheld Gemini events was all extraction drift: "ransomware
attacks", "State of Israel" and "IT infrastructure", each quoted verbatim from
both years.

The response counts withheld events (`withheld`, under the same filters), and
the page says how many it isn't showing.

## What the NVIDIA corpus produces

`tests/fixtures/edgar-timeline`: Risk Factors from the FY2024, FY2025 and FY2026
10-Ks, verbatim and on their real pages, filtered to paragraphs naming the demo
vocabulary. Extracted with the keyword matcher (`tools/seed_demo.py`), not Claude.

| filed | event | paragraphs | evidence |
|---|---|---|---|
| Feb 2026 | H20 Newly Disclosed | 0 → 4 | p. 26, the April 2025 license requirement |
| | Export Controls Expanded | 20 → 26 | p. 27, "The export controls applicable to China are complex…" |
| | China Expanded | 17 → 22 | p. 26, the April 2025 license requirement |
| | AI Diffusion Rule Narrowed | 10 → 2 | p. 27, "In January 2025, the USG published the AI Diffusion IFR…" |
| | DGX Cloud No Longer Disclosed | 1 → 0 | p. 15 of the FY2025 10-K |
| Feb 2025 | AI Diffusion Rule Newly Disclosed | 0 → 10 | p. 26 |
| | Blackwell Newly Disclosed | 0 → 2 | p. 17 |
| | Export Controls Expanded | 13 → 20 | p. 27, "Over the past three years… shifting and expanding export controls" |
| | Tariffs Expanded | 2 → 7 | p. 17 |
| | China Expanded | 13 → 17 | p. 25 |
| Feb 2024 | FY2024 10-K Filed (baseline) | | |

`tests/test_timeline_e2e.py` asserts this list exactly, and that every
evidence citation resolves through `/v1/evidence` to the same page and to text
that names the topic.

## Computed on read

Events are derived from `documents`, `entity_mentions` and the filings' text,
at request time, not stored. That is what makes the
feature nearly free: no second copy of the truth to keep in sync, no backfill
when extraction improves, and a re-extracted filing changes the timeline the
moment it lands. Each filing's text is indexed once per process
(`timeline.said`, an LRU of 64 filings), since the same filings are compared
on every request. The three-filing demo takes about 90 ms on the first
request and 20 ms after. If a company with decades of filings makes it slow, cache
the response per company keyed on the newest extraction — do not store
events.

The existing `timeline_events` table stays what it is — a raw event log, served
at `/v1/companies/{ticker}/timeline`.

## API

    GET /v1/company/NVDA/timeline?category=risk&kind=expanded&entity=china&since=2025-01-01&limit=100

All filters apply **after** the comparison: `since=2026-01-01` returns FY2026's
events as compared with FY2025, not a timeline in which FY2026 is the baseline.
`categories` counts events matching every filter except `category`, so filter
chips can show what each would return. Unknown `kind` → 422; unknown ticker →
404.

Each event has `id` (accession, kind and slug — stable across rebuilds),
`date`, `date_label`, `kind`, `category`, `title`, `summary`, `topic`, `filing`,
`compared_with`, `paragraphs`, `previous_paragraphs` and `evidence`
(`chunk_id`, `paragraph_id`, `document_id`, `page`, `quote`, `new_paragraph`,
`citation`). Filings link to their EDGAR index page, whatever origin they were
ingested from. `withheld` counts the changes the text did not bear out (see
"Checked against the text"). `thresholds` reports `min_delta`, `min_ratio` and
`carried_over`.

## What it cannot tell you

- **New and dropped rest on extraction, checked by the text.** A drop means
  no paragraph of the new filing was extracted as citing the topic, and none
  of last year's quotes for it survives. That catches a missed mention whose
  sentence is still there. It cannot catch one whose sentence was rewritten.

  Before the text check, real extraction (Gemini 3.5 Flash-Lite, the three
  demo 10-Ks, 2026-11-21) gave 131 events against the keyword matcher's 13.
  Of its 54 "no longer disclosed", 38 pointed at a paragraph still in the
  newer filing. After the check, 64 events remain.
- **Spelling variants of one name are still two topics.** "GB200 NVL 72" is
  dropped and "GB200 NVL72" is new where the sentence around it was
  rewritten; "datacenter GPUs" and "data center GPUs" likewise. "U.S." now
  folds into "United States", as "US" did. Other variants need a resolver
  that knows more than spelling.
- **Extraction picks some poor topics.** "Charge", "non-data center" and
  whole phrases ("carbon taxes, fuel or energy taxes, pollution limits, …")
  are real text, quoted correctly, but weak topics. That is a prompt problem,
  not a timeline one.
- **Counts measure emphasis, not stance.** Export Controls Expanded says the
  filing discusses export controls in more paragraphs; it does not say the
  controls got stricter. The evidence paragraph is where to read which.
- **One company.** The thresholds and the carried-over cutoff were tuned on
  NVIDIA. They are constants, reported in the response, for that reason.
