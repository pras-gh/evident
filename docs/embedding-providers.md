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
