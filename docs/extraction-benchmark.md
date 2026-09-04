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
