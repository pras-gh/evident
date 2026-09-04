# Extraction benchmark

One purpose: measure extraction quality against reality.

Everything before this measured whether the pipeline *runs*. This measures
whether what comes out of it is any good — on real filing text, with the
numbers written down so the next prompt change can be compared against them
rather than argued about.

## What a run does

```
one real 10-K section  ->  N chunks  ->  N Claude calls
                                          |
                        raw response stored verbatim
                                          |
                        Pydantic -> validated entities -> Postgres
                                          |
                              report: rates, tokens, cost
```

## Why the raw response is stored

`extraction_runs` keeps the exact text Claude returned, per chunk, alongside
the prompt version, model, token counts and latency.

Three reasons, and the third is the one that matters.

**Re-validation without re-paying.** A schema change can be replayed against
every stored response offline. Finding out that a new `entity_type` would have
caught 40 more entities costs nothing.

**Regression comparison.** "The drop rate went from 4% to 19%" is only
answerable if the old responses still exist. Prompt version and model are
stored per row, so a change in either is visible as the cause rather than
guessed at.

**Auditing a wrong answer.** When an entity in the graph turns out to be wrong,
the question is whether the model said something wrong or we stored it wrong.
Without the raw response those are indistinguishable, and the pipeline gets
blamed for a prompt problem or vice versa.

## The metrics

| metric | what it means | why it is the one to watch |
|---|---|---|
| response rejection rate | responses refused whole | non-zero means truncation, refusal, or a schema mismatch — a bug, not a quality signal |
| item drop rate | entities and edges refused individually | the quality signal. Mostly hallucinated citations |
| entities per chunk | yield | a collapse here after a prompt change means the model got more conservative |
| relationships per chunk | edge yield | typically far lower than entities; most paragraphs assert none |
| confidence distribution | how sure the model says it is | a distribution pinned at 0.95+ means the model is not discriminating, which is worse than a spread |
| cache hit rate | share of input tokens read from cache | should approach the steady state after the first chunk |
| cost per chunk | what a full filing would cost | the number that decides whether the batch path is mandatory |

## The corpus

`tests/fixtures/edgar-bench/` — verbatim paragraphs from Item 1A, Risk Factors,
of NVIDIA's FY2025 Form 10-K, accession `0001045810-25-000023`.

Risk Factors was chosen deliberately. It is the densest part of a filing for
this taxonomy — it names products, geographies, other companies, supply
constraints and strategies in tight prose — and it is the section where a
missed entity matters most, because it is where the company writes down what
could go wrong.

The paragraphs are unedited. Sections are the filing's own risk headings.

## Running it

```bash
export ANTHROPIC_API_KEY=...
export DATABASE_URL=postgresql+psycopg://localhost/evident
python tools/benchmark.py --seed            # ingest the corpus, then extract it
python tools/benchmark.py --report-only     # re-render, no API calls
```

`--seed` ingests the corpus over a local origin, so the run needs no sec.gov
access. It produces **14 chunks**. The report lands in `docs/benchmarks/<run
id>.md`; commit it, because a benchmark that exists only on the machine that
produced it cannot be compared with anything.

The tool drives `build_for_document` with a recorder attached rather than
reimplementing extraction, so a green benchmark says the pipeline works, not
that a benchmark script works.

## Verified

- migration `0007` applied; `db/schema.sql` and `alembic upgrade head` produce
  identical 10-table schemas
- the corpus ingests to 14 chunks from 32 verbatim Risk Factors paragraphs
- 210 tests pass
- the recorder fires for accepted *and* rejected chunks, and a rejection
  carries its raw text, latency and token cost — a response that failed to
  parse is the most useful artefact there is when working out why
- `--report-only` regenerates a report from stored rows with no API call

### Found while building it

Rejected responses and dropped items were sharing one `reasons` list, so the
worker logged `"rejected extraction: 'Ghost' cites paragraph 999_9"` — calling
a dropped citation a rejected response. They have different causes and
different fixes, and that log would send someone hunting a truncation that
never happened. `DropReport.rejections` is now separate from
`DropReport.reasons`.

## Not run

No API call has been made — there are still no Anthropic credentials in this
environment. The tool was exercised against a stub that returns varied
confidences, one truncated response and one hallucinated citation, which proved
the rows are written, the rates compute, the rejection path records its raw
text, and the report renders every branch.

That is the instrument working. **There is no benchmark yet** — the numbers a
stub produces are not a measurement of anything, and no report has been
committed for exactly that reason.
