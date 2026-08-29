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
