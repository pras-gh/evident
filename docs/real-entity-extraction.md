# Real Entity Extraction

Extraction goes through the AI gateway (`docs/ai-gateway.md`). The task
`financial_extraction` is routed by `config/ai.toml`, Gemini 3.5 Flash-Lite by
default. No free-form output anywhere in the path.

```
chunk  ->  gateway  ->  JSON Schema  ->  Pydantic  ->  entities + relationships  ->  Postgres
```

## Never free-form

Three gates, and each one catches something the others cannot.

**1. The API is constrained.** The request carries `output_config.format` with a
JSON Schema derived from the Pydantic models. `entity_type` is an `enum`,
`additionalProperties` is `false`, and required fields are required. The model
does not choose a shape; it fills one in.

**2. Pydantic parses the response.** `EntityExtractionResponse` is the only way
a response becomes objects. A response that will not parse is rejected whole —
not partially read, not repaired, not "best effort". There is no code path from
raw model text to the database.

**3. Per-item validation, after parsing.** Some rules cannot be expressed in a
schema, because they depend on the input we sent:

- an entity must cite a `paragraph_id` we actually supplied;
- its quote must be in that paragraph. Matching allows for case, curly quotes,
  dashes and whitespace, and the stored quote is the sentence that contains
  it (see "Prompt v4" below);
- its name must be a topic, at most `MAX_NAME_WORDS` (6) words;
- a relationship's two endpoints must both be entities in the same response,
  and its quote must be in its paragraph too.

These are checked per item and violators are dropped and counted, rather than
failing the batch — one hallucinated citation should not cost the other
nineteen good entities from that chunk.

## What "reject malformed JSON" means in practice

With structured outputs the API guarantees schema-valid JSON, so malformed
output is not the common case — but it is reachable, and each way has a
different fix:

| failure | cause | handling |
|---|---|---|
| truncated JSON | `stop_reason == "max_tokens"` | reject, count, raise on the batch |
| no text block | `stop_reason == "refusal"` | reject, record the refusal category |
| parse error | anything else | reject the whole response, keep the raw text for diagnosis |

A rejected response never yields partial entities. The alternative — salvaging
what parses — means storing an entity from a response we know was cut off,
which is the kind of data that looks fine until someone audits it.

## Prompt v4: names that persist, evidence that reads

v3.1 on Gemini 3.5 Flash-Lite, over the three demo 10-Ks, had three problems:

- **Quotes were bare names.** 84% were under four words, with a median of two.
- **Names were sometimes phrases**, up to 17 words: "loss of data or other
  information technology assets".
- **A topic's name drifted between years.** The timeline then reported it as
  dropped under one name and new under another.

v4 (`extract_entities@4.0.0`) makes three changes.

**Known topics in each request.** The memory builder passes the company's topics
on file, most-cited first. Each request lists up to 40 of those that share a
meaningful word with its paragraphs, and the prompt says to reuse a listed name
exactly when it is the same thing. Filings are extracted oldest first, so each
year can reuse the last year's names. The system prompt is unchanged per
request; the list goes in the user message.

**Short names.** The prompt asks for one to five words, and validation drops
names over six as a backstop.

**Quotes anchored in code.** The model still returns a short span, which keeps
output tokens, the expensive side, low. The code finds the span in the cited
paragraph and stores the whole sentence that contains it. Matching allows for
case, curly quotes, dashes, whitespace and "..." between verbatim fragments.
A quote found in the other part of a paragraph the chunker split moves the
citation there. A quote that isn't found drops its item with a reason; nothing
checked this before. A sentence over 600 characters keeps the span.

Measured on the same corpus and model, 2026-11-21:

| | v3.1 | v4 |
|---|---|---|
| quotes under 4 words | 84% (median 2 words) | 5% (median 35) |
| names over 5 words | 10 | 0 |
| entities / mentions / relationships | 140 / 551 / 8 | 87 / 623 / 27 |
| topics in all three filings | 51 | 62 |
| timeline: changes shown / withheld by the text check | 61 / 67 | 38 / 4 |
| items dropped by validation, final run | — (quotes not checked) | 6 |
| cost of the full extraction, list price | $0.12 | $0.16 |

The drops seen are real misquotes: four relationship quotes reading "negative
impact demand" where the filing says "negatively impact demand", and a quote
absent from the paragraph cited. No v4 answer had a name over six words (0 of
136), so that check is purely a backstop.

**Known topics are part of the input, so they are part of the cache key.** A
filing's cached answers hold as long as the earlier filings' topics don't
change. If an earlier filing gains or loses a topic, for example because a
chunk that failed last time succeeds this time, later filings are extracted
again. That's correct, but it is why background requests now retry hard
(`docs/ai-gateway.md`, Reliability): a run that completes is a run whose
re-run costs $0. That held here; the second run of the complete seed cost
nothing.

## Relationships come from the same call

`EntityExtractionResponse` carries both lists, so the model names an entity and
the edges it participates in while it still has the paragraph in front of it.
Asking separately meant re-reading the same text to rediscover the endpoints.

Edges reference entities **by name**, resolved to ids after the entities are
upserted. An edge whose endpoint did not survive validation is dropped — an
edge to nothing is not an edge.

These are typed, asserted relationships, and they are stored alongside the
co-occurrence edges the graph engine already derives. Both carry
`evidence_chunk_id`, so every edge can be shown the sentence it came from.

## Cost

Prompt caching on the system prompt, and the Batch API for whole-filing runs at
half price. Both were built in Phase 1; this phase makes them the default path
for a full document rather than a per-chunk call.

## Verified

Everything below the HTTP boundary is exercised by tests; the boundary itself
is faked, because a fake response is the only way to assert how a *bad* one is
handled.

- 156 unit tests, 21 integration tests against real Postgres
- the whole path persists: chunk -> fake Claude -> Pydantic -> validation ->
  entities, mentions, and an edge carrying its `evidence_chunk_id`
- an edge whose source was never extracted is dropped, not stored
- a truncated response writes **nothing** — asserted by counting rows before
  and after
- every rejection path covered: truncation, refusal, malformed JSON, unknown
  enum value, out-of-range confidence, and a field we never asked for
- 474 chunks ingested into a real database, and `tools/extract_live.py
  --dry-run` renders the exact request against them

### The whole-pipeline test, and what it caught

`tests/test_pipeline_e2e.py` runs the real thing end to end —
**ingest -> extract -> persist -> `GET /v1/companies/NVDA/memory`** — with only
the Claude call faked, and faked against real ingested data: the response cites
paragraph ids read back out of the database after ingestion, so the citation
guard is doing real work. A response citing invented ids would be dropped and
the test would fail.

It found a bug on its first run. `build_for_document` was sending **the entire
filing in a single request** — every chunk's blocks in one call. A 474-chunk
10-K is roughly 284K input tokens asking for entities across all 474 chunks
against `max_tokens=16000`; the response truncates, gets rejected whole, and
the filing stores nothing. Meanwhile `extract_document` and `submit_batch`
already existed to chunk the work properly and **nothing called either of
them**.

It is now one request per chunk, which is also what makes the rejection
semantics work as designed: one bad response costs that chunk, not the other
473. `submit_batch` remains the half-price path for backfills.

No per-stage test could have caught this. Each one builds its own fixtures
rather than consuming the previous stage's output, so none of them ever handed
the extractor a whole document.

### Found while doing this

`build_for_document` had never run. It assigned `block.chunk_hash` onto a
slots dataclass, which always raises `AttributeError` — meaning the worker that
writes memory to Postgres was never once executed end to end, by a test or
otherwise. The line was vestigial from the previous extractor; the chunk is
recovered through `by_paragraph`, which the mention write already used. The new
integration test is what surfaced it.

## Still not proven

**Claude has never been called.** Extraction has run live on Gemini 3.5
Flash-Lite only: `tools/extract_live.py` on three chunks, and the whole demo
corpus through `tools/seed_demo.py --extract gateway`. Routing it to Claude
is one line in `config/ai.toml`, but the Anthropic adapter has only been tested
against mocked HTTP.

**Full 10-Ks have not been extracted.** The real filings on disk are excerpts:
Risk Factors sections, 94 chunks across three years. A full 10-K is about 470
chunks, and sec.gov blocks this network at the edge.

## The live run

`tools/extract_live.py` drives the real pipeline and checks each success
criterion:

```bash
export GEMINI_API_KEY=...           # whichever key the financial_extraction route needs
export DATABASE_URL=postgresql+psycopg://localhost/evident
python tools/extract_live.py --ticker NVDA --limit 3 --seed --verify
```

`--seed` ingests `tests/fixtures/edgar-real/` — verbatim paragraphs from
NVIDIA's FY2025 10-K, accession `0001045810-25-000023`, three sections and
therefore three chunks. It exists because the sibling `edgar/` fixture is
synthetic word-salad: right for testing the parser, useless for testing
extraction. Seeding locally also means the run does not need sec.gov, which
blocks many networks at the edge.

`--verify` prints each criterion with a PASS/FAIL and exits non-zero on
failure. Run it twice: the first run stores each answer in the gateway's
response cache, and the second is answered from it for $0.

### The cache criterion needed a fix first

"Second run hits cache" would have failed for a reason that is not a bug: the
cached prefix only engages above roughly 1024 tokens, and the system prompt was
4,261 characters... after this change. It was 3,429 — about 857 tokens, under
the floor, so nothing would ever have been cached.

The fix is a worked example in the prompt rather than padding: the four
entities and one relationship from the spec's own NVIDIA sentence, plus what a
correct response *omits* (the filer is not a `company`, and two things named in
one sentence are not a relationship). Few-shot examples improve structured
extraction on their own; clearing the cache floor is the second benefit, not
the reason. The run reports actual cached tokens either way, so the criterion is
measured rather than assumed.

### Run live on Gemini

On 2026-11-21, three chunks gave 0 rejected responses, 23 entities and 1
relationship for $0.0056. The re-run was answered from the cache. Seeding the
three demo 10-Ks through the gateway extracted all 94 chunks.
