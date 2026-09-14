# Real Entity Extraction

Everything runs against Claude. No free-form output anywhere in the path.

```
chunk  ->  Claude API  ->  JSON Schema  ->  Pydantic  ->  entities + relationships  ->  Postgres
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

**3. Per-item validation, after parsing.** Two rules cannot be expressed in a
schema, because they depend on the input we sent:

- an entity must cite a `paragraph_id` we actually supplied
- a relationship's two endpoints must both be entities in the same response

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

**The live call has not happened.** There are no Anthropic credentials in this
environment, so every test drives a fake client. The request shape, schema,
caching block and batch path follow the SDK reference, but nothing has been
sent. Two things can only be measured on a real run: the drop rate, and whether
the system prompt is long enough to cache — at 3,429 characters it is roughly
900 tokens, likely just under the ~1024-token minimum, in which case caching
saves nothing and `cache_hit_rate()` will report zero.

**The only filing on disk is synthetic.** `tests/fixtures/edgar/...` has real
inline-XBRL structure and word-salad prose, which is right for testing the
parser and useless for testing extraction. A real run needs a real filing
ingested from SEC, which this network blocks at the edge.

## The live run

`tools/extract_live.py` drives the real pipeline and checks each success
criterion:

```bash
export ANTHROPIC_API_KEY=...        # or: ant auth login
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
failure. Run it twice — the first run writes the prompt cache, the second reads
it.

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

### Not run against Claude

No API call has been made. There are no Anthropic credentials in the
environment this was built in — `api.anthropic.com` answers, it just answers
401 — so the command above has never been executed for real.

The harness itself was exercised against a stub that returns canned responses
with token counts, which proved the report renders, entities deduplicate across
chunks, edges persist with their evidence chunk, and a second run adds no
duplicate rows. That is the plumbing working. It is not evidence that Claude
returns good entities, and nothing here should be read as if it were.
