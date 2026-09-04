# Embedding providers

One interface, three implementations, chosen by configuration.

```python
class EmbeddingProvider:
    name: str
    model: str
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]
```

| implementation | model | when |
|---|---|---|
| `VoyageEmbedder` | `voyage-finance-2` | default. Finance-tuned retrieval. |
| `OpenAIEmbedder` | `text-embedding-3-large` | fallback. |
| `HashingEmbedder` | `hashing-v1` | **tests only.** No semantics. |

Switching is one environment variable:

```bash
EMBEDDING_PROVIDER=voyage     # or: openai, hashing
```

## Why voyage-finance-2

Voyage publishes it as optimised for finance retrieval and RAG, which is
exactly this corpus — 10-Ks, earnings calls, risk factors. `text-embedding-3-large`
is the fallback because it is strong, general, and operationally boring. The
point of the abstraction is that neither choice is load-bearing.

## The dimension is not a free choice

`chunks.embedding` is a fixed-width pgvector column, so every provider must
emit the same width or the schema stops matching.

| model | default | can emit |
|---|---|---|
| `voyage-finance-2` | 1024 | **1024 only** |
| `voyage-4-large` | 1024 | 256, 512, 1024, 2048 |
| `text-embedding-3-large` | 3072 | any width, via `dimensions` |

**1024 is the only width all three can produce.** The column was 1536 — a width
Voyage cannot emit at any setting, which would have made "switch the provider"
a schema migration rather than a config change. Revision `0006` narrows it to
1024, and both real providers are configured to emit exactly that.

The provider asserts the width it got back matches the width it promised. A
silent mismatch is either a Postgres error much later or, worse, an index built
on vectors that mean something different from the ones already in it.

## Switching providers is a re-embed, not a flip

The config change is one line. The *data* change is not, and pretending
otherwise is how a search index quietly starts lying.

Vectors from different models are not comparable. Cosine distance between a
Voyage vector and an OpenAI vector is a number with no meaning — it will not
error, it will just rank wrongly. Every row records `embedding_provider` and
`embedding_model`, and the retrieval layer refuses to search across a mixed
index rather than returning confident nonsense.

So a provider switch is: change the variable, re-embed the corpus, then query.

## Why HashingEmbedder is no longer the default

It was a credential-free stand-in that made the pipeline runnable end to end.
It carries no semantics — cosine similarity over it reflects vocabulary
overlap, nothing more — so any retrieval result it produced was shaped like an
answer without being one.

It is now reachable only by naming it explicitly. `default_provider()` raises if
nothing is configured, because a search index silently full of hash vectors is
worse than one that is obviously empty.

## Query vectors are embedded differently

Voyage distinguishes `input_type="document"` from `input_type="query"`, and
using the right one measurably improves retrieval. `embed()` is the document
path; `embed_query()` is the query path and defaults to `embed()` for providers
that draw no distinction.

## Verified

- 179 unit tests, 22 integration against real Postgres
- every provider defaults to the shared width, and the width matches the column
- a provider returning the wrong width, or the wrong count, raises before
  anything reaches the database
- Voyage sends `input_type=document` for chunks and `query` for searches,
  omits `output_dimension` for `voyage-finance-2` (which rejects it) and sends
  it for the general models, and batches at the documented 1,000-text limit
- OpenAI requests `dimensions=1024` explicitly and sorts results by the `index`
  the API returns rather than trusting arrival order
- `default_provider()` raises when unconfigured — asserted, not assumed
- revision `0006` applied to a database holding a real 1536-wide vector: the
  vector and its provenance are cleared and the column is `vector(1024)`
- `db/schema.sql` and `alembic upgrade head` still produce identical 9-table
  schemas, both with `vector(1024)`

Provider dimensions and batch limits were read from Voyage's and OpenAI's
current documentation during this change, not recalled.

### Found while doing this: dead retrieval code, now deleted

`packages/retrieval/search.py` and `store.py` were written against the
superseded raw-SQL schema — they queried `chunk_embeddings`, `sections`,
`blocks` and `filing_tables`, none of which exist. Neither could run. Nothing
noticed, because the only symbols anything imported were `rerank` and
`_vector_literal`, and the retrieval tests exercised a parallel `Hit` class
rather than the shipped search path. The ranking was covered; the ranking that
ships was not.

Both files are **deleted**, not quarantined. A second search implementation
that cannot run is not a fallback, it is a trap — the next person to fix a
ranking bug has even odds of fixing the wrong one.

What survived is the part worth keeping, and it is now live rather than
shelved:

- `rerank` moved to `evident_retrieval/rerank.py` and is **wired into
  `POST /v1/search`**, which now over-fetches `k * 3` and blends recency into
  the ordering. Previously the endpoint ranked on cosine distance alone while
  a tested recency-weighting function sat unused next door.
- `_vector_literal` is gone: pgvector's SQLAlchemy type serialises a list
  itself, so it only ever existed for the raw-SQL path.
- `Chunk.citation()` is now tested. The old citation test covered a `Hit.citation`
  that nothing called, while the method the search endpoint actually renders had
  no coverage at all.

`packages/retrieval/` is three files: `embed.py`, `rerank.py`, `__init__.py`.
There is no `live/` and `legacy/` split because there is no longer a legacy
half to separate.

## Not proven

No embedding API has been called. There are no Voyage or OpenAI credentials
here, so both providers are driven by fake clients. What is asserted is the
shape of the request and the handling of the response; what has not been
observed is a real vector.
