# Phase 1 — Entity Extraction

The brain. One chunk of filing text goes in, canonical typed entities come out,
each one carrying the paragraph it came from.

```
"NVIDIA continues investing in AI infrastructure to accelerate Blackwell
 deployment despite export restrictions into China."

  → Blackwell           product     0.99
  → AI Infrastructure   strategy    0.97
  → China               geography   0.98
  → Export Restrictions risk        0.95
```

## Canonical entity types

Every entity belongs to exactly one of eight types. A closed set is the point:
an open `type` field produces `strategy`, `Strategy`, `strategic-initiative`
and `theme` for the same idea within one filing, and nothing downstream can
group them.

| type | examples |
|---|---|
| `strategy` | AI Infrastructure, Cost Optimization |
| `product` | Blackwell, CUDA, Copilot |
| `executive` | Jensen Huang, Tim Cook |
| `risk` | Export Controls, Supply Chain |
| `metric` | Revenue, Gross Margin, CapEx |
| `segment` | Gaming, Data Center |
| `company` | Microsoft, AMD, Amazon |
| `geography` | China, Europe, US |

The set is enforced twice: as an `enum` in the extraction schema, so the model
cannot return anything else, and as a `CHECK` constraint, so no other writer
can either.

## Reconciling with the shipped schema

The entity tables that landed in #8 are the same three tables under different
names. This phase renames rather than rebuilds, so no data is lost.

| spec | shipped | resolution |
|---|---|---|
| `name` | `label` | rename to `name` |
| `slug` | `key` | rename to `slug` |
| `entity_type` | `kind` | rename; retype (see below) |
| `description` | — | new column |
| `importance_score` | — | new column; the graph engine computes it but never persisted it |
| `first_seen` / `latest_seen` | `first_seen_at` / `last_seen_at` | rename |
| mentions `page` | `page_number` | rename |
| relationships `relationship_type` | `kind` | rename |
| relationships `strength` | `weight` (int) | rename; widen to float |
| relationships `evidence_chunk_id` | `document_ids[]` | add the column; keep the array |

### Type migration

`strategy`, `product`, `risk`, `metric` and `segment` carry over unchanged.
`person` becomes `executive`. `company` and `geography` are new. `topic` folds
into `strategy` — it was always the catch-all, and every topic the graph engine
currently holds is a strategy in the new taxonomy. `event` has its own table
(`timeline_events`) and is dropped from the entity types rather than duplicated.

### One deliberate departure: `slug TEXT UNIQUE`

The spec says `slug TEXT UNIQUE`, globally. That works for exactly one company.

`entities` is company-scoped — it carries `company_id` — and every company on
earth has a `revenue` metric. The second company ingested would collide on the
first slug it shares with the first, and the insert would fail. `China` as a
geography is the same story across any two companies that both sell there.

So the constraint is `UNIQUE (company_id, slug)`: globally unique *per company*,
which is what the column is actually for. Note this also makes a name resolve to
one type per company — `Data Center` cannot be a `segment` in one filing and a
`product` in the next — which is the invariant a canonical entity table wants.

## Cost

A 10-K is ~275 chunks. One request per chunk with an uncached system prompt is
275 × the full taxonomy, examples and rules.

Two mitigations, both in `extraction.py`:

- **Prompt caching** on the system prompt, which is byte-identical across every
  chunk in the corpus. Cached reads are ~10% of input cost.
- **The Batch API** for whole-filing ingestion, at 50% of standard price, since
  nothing about backfilling a 2019 10-K is latency-sensitive.

## The guardrail stays

`drop_uncited()` predates this phase and survives it. The model is handed
paragraphs that already have ids and must cite one per entity; anything citing
an id we did not supply is dropped before storage, and counted. A hallucinated
citation is worse than a missing entity — it looks exactly like a real one until
someone clicks through.
