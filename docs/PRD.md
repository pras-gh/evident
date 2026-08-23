# Evident — product requirements

## The problem

Public companies disclose enormous amounts of information and almost none of it
is usable. A ten-year corpus for one company is thousands of pages across 10-Ks,
10-Qs, 8-Ks, proxy statements, earnings call transcripts and investor decks.
Analysts read a fraction of it, remember less, and re-derive the same context
every quarter.

Existing tools fail in two distinct ways.

**Search tools** return passages. A passage answers "where is this mentioned",
not "when did this start" or "did they deliver what they said". The reader still
has to hold the history in their head.

**AI summarisers** return prose with no traceable source. In a domain where
being wrong has financial consequences, an unverifiable summary is worse than no
summary — it is confidently shaped like an answer.

## What Evident is

A durable, evidence-backed **memory** for every public company, built
incrementally from every document it has ever filed.

Two claims define the product, and both are falsifiable:

1. **Every answer cites a page and a paragraph.** Not a document. Not a
   similarity score. The exact span, highlighted in the original filing.
2. **The memory has a time axis.** Topics have a first mention. Metrics are
   series with restatements flagged. Risk factors that stop being disclosed are
   marked, not deleted. Commitments are carried until something settles them.

## What it is not

**Not a vector database.** Embeddings are an index over evidence, one layer down.
The unit of value is the company, not the chunk. "When did they first mention
Blackwell" and "who has run this segment" are not similarity questions.

**Not a chatbot over PDFs.** The memory is built ahead of the question, is
inspectable without asking anything, and is the same for every reader.

**Not investment advice.** It finds and cites what companies said in public. The
judgement stays with the reader, and the product is careful never to imply
otherwise.

## Users

| User | The job |
| --- | --- |
| Buy-side / sell-side analyst | Get current on a name in an hour instead of a week; catch what changed between filings |
| Investor relations | See how one's own disclosure has drifted; find what was committed to and when |
| Financial journalist | Establish when a company started saying something, with a citation that survives editing |
| Retail investor | Read a company's actual record instead of its narrative |

## The primitives

### Memory cards

The surface a reader lives in. One card per dimension — Revenue, Products,
Guidance, Risks, CapEx, Litigation — each bound to the part of the corpus that
updates it.

| Card | Updates from |
| --- | --- |
| Revenue | 10-Q / 10-K |
| Products | Earnings call |
| Guidance | CEO statements |
| Risks | Risk section |
| CapEx | Cash flow section |
| Litigation | Legal section |

**A card is not a current value. It is an append-only series of revisions**, one
per filing that touched it, each carrying a diff. `Revenue $394.3B` is a number
every dashboard shows. That it has been revised four times, three of them
materially, is not.

The most valuable output of a card is often a *removal*: "1 no longer disclosed:
China export controls." A risk factor quietly disappearing between two 10-Ks is
not something search returns.

### Promises

Forward-looking commitments, carried until a later filing settles them. This is
the entity no similarity index can represent, and the one where being careful
matters most.

**A promise is never marked `broken` without evidence.** A company going quiet is
suggestive, not probative. Silence past the horizon becomes `unclear` — and an
unresolved commitment past its due date is itself the finding.

### Topic graph

Topics wired to the documents that discuss them, with edges weighted by shared
documents rather than text similarity — so every edge can name the filings that
produced it. Sliceable by date, which is what makes "watch this company's story
unfold" a real feature rather than an animation.

## How we will know it works

| | Target | Why this one |
| --- | --- | --- |
| Uncited claims in production | **0** | The core promise. One uncited claim on screen falsifies the product |
| Extraction citation drop rate | < 2%, alerting on rise | A rising rate means extraction started inventing ids — the failure that looks like success |
| Time to first cited answer | < 60s from question | The pitch |
| Card revisions per filing | ≥ 1 per routed card | If a filing lands and no card moves, routing is broken and nobody would notice |
| Coverage | 5,000+ US-listed, ~20 years | Table stakes against incumbents |

The second row matters more than it looks. Every other failure mode is visible;
a hallucinated citation looks exactly like a real one until someone clicks it.

## Sequencing

**Now.** Ingestion of 10-K / 10-Q / 8-K. Memory for topics, metrics, risks,
promises, products. The six cards. Evidence viewer.

**Next.** Earnings call transcripts with speaker attribution — which unlocks the
Guidance card's real source and dated executive roles. Promise resolution
backfill. Cross-company compare.

**Later.** Alerting on card changes ("tell me when a risk factor disappears").
Notebook. Non-US filings.

## Open questions

- **Promise resolution cost.** Running a resolution pass on every new filing is
  expensive; running it only when a horizon lapses is cheap but lags. Leaning
  toward a nightly sweep over lapsed horizons, but it is a freshness/cost call
  that should be made deliberately.
- **Embedding provider.** Deliberately not chosen. Provider, model and dimension
  are stored beside every vector so the decision stays reversible.
- **Which "unclear" promises to surface.** Every lapsed commitment is a finding
  in principle; in practice a company with 40 open promises would drown the card.
  Needs a relevance rule before this ships to anyone.
