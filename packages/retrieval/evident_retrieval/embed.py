"""Embedding providers — one interface, chosen by configuration.

    EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]

Three implementations. `VoyageEmbedder` is the default because Voyage publishes
`voyage-finance-2` as tuned for finance retrieval, which is exactly this corpus.
`OpenAIEmbedder` is the fallback. `HashingEmbedder` is for tests and is no
longer reachable without asking for it by name.

Two things this abstraction deliberately does not hide.

**The dimension is shared.** `chunks.embedding` is a fixed-width pgvector
column, so every provider emits 1024 — the only width all three can produce,
since `voyage-finance-2` supports 1024 and nothing else. A provider that
returns a different width raises here rather than at the database, or worse,
into an index whose vectors no longer mean the same thing.

**Switching is a re-embed, not a flip.** Vectors from different models are not
comparable. Cosine distance between a Voyage vector and an OpenAI vector is a
number with no meaning; it will not error, it will just rank wrongly. Every row
records the provider and model that produced it so a mixed index can be
detected instead of silently answered.
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Sequence

#: The one width every supported provider can emit. `voyage-finance-2` is
#: 1024-only, OpenAI can shorten to anything, Voyage's general models offer
#: 256/512/1024/2048 — so 1024 is the intersection. Changing this is a schema
#: migration and a full re-embed, not a constant edit.
EMBEDDING_DIM = 1024


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider:
    """The interface. `name`, `model` and `dim` are recorded next to every vector.

    They are attributes rather than part of the return value so `embed` keeps
    the plain shape callers want, while the row still knows what produced it —
    which is the only thing that makes a later provider migration mechanical
    rather than archaeological.
    """

    name: str = "unset"
    model: str = "unset"
    dim: int = EMBEDDING_DIM

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed documents."""
        raise NotImplementedError

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed search queries.

        Some providers distinguish the two and retrieve measurably better for
        it. Those that do not inherit this, which is the document path.
        """
        return self.embed(texts)

    # -- shared plumbing -------------------------------------------------
    def _check(self, vectors: list[list[float]], expected: int) -> list[list[float]]:
        if len(vectors) != expected:
            raise EmbeddingError(
                f"{self.name}: asked for {expected} vectors, got {len(vectors)}")
        for i, v in enumerate(vectors):
            if len(v) != self.dim:
                raise EmbeddingError(
                    f"{self.name}/{self.model}: vector {i} has {len(v)} "
                    f"dimensions, expected {self.dim}. The column is fixed "
                    "width; storing this would corrupt the index.")
        return vectors

    @staticmethod
    def _batched(texts: Sequence[str], size: int):
        for i in range(0, len(texts), size):
            yield texts[i:i + size]


class HashingEmbedder(EmbeddingProvider):
    """Deterministic bag-of-hashed-tokens vectors. **Tests only.**

    No network, no credentials, and no semantics — cosine similarity here
    reflects vocabulary overlap and nothing else. It exists so the pipeline is
    runnable in CI without an account.

    It used to be the default. It is not any more: an index silently full of
    hash vectors returns things shaped like answers, which is worse than
    returning nothing. `default_provider()` will not hand you one unless you
    name it.
    """

    name = "local"
    model = "hashing-v1"

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._check([self._one(t) for t in texts], len(texts))

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in text.lower().split():
            h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            vec[idx] += 1.0 if h[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


class VoyageEmbedder(EmbeddingProvider):
    """Voyage AI. Default, on `voyage-finance-2`.

    `voyage-finance-2` is published as optimised for finance retrieval and RAG,
    and it is fixed at 1024 dimensions — which is why 1024 is the width the
    whole system uses.

    Voyage distinguishes document embeddings from query embeddings, and using
    the right `input_type` for each is a free retrieval improvement.
    """

    name = "voyage"
    #: The API accepts at most 1,000 texts per request.
    MAX_BATCH = 1000

    def __init__(self, model: str = "voyage-finance-2",
                 dim: int = EMBEDDING_DIM, *, api_key: str | None = None,
                 client: object | None = None) -> None:
        self.model, self.dim = model, dim
        self._api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import voyageai
        except ImportError as exc:  # pragma: no cover - env dependent
            raise EmbeddingError(
                "VoyageEmbedder needs the voyageai package — "
                "`pip install voyageai`"
            ) from exc
        if not self._api_key:
            raise EmbeddingError(
                "VOYAGE_API_KEY is not set. Set it, or choose another "
                "provider with EMBEDDING_PROVIDER.")
        self._client = voyageai.Client(api_key=self._api_key)
        return self._client

    def _embed(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        out: list[list[float]] = []
        for batch in self._batched(texts, self.MAX_BATCH):
            kwargs = {"texts": list(batch), "model": self.model,
                      "input_type": input_type}
            # voyage-finance-2 is 1024-only and rejects the parameter; the
            # general models need it to come back at the shared width.
            if self.model != "voyage-finance-2":
                kwargs["output_dimension"] = self.dim
            out.extend(client.embed(**kwargs).embeddings)
        return self._check(out, len(texts))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "query")


class OpenAIEmbedder(EmbeddingProvider):
    """OpenAI. The fallback, on `text-embedding-3-large`.

    The model returns 3072 dimensions by default; `dimensions` shortens it to
    the shared width. That truncation is supported by the model rather than
    something we do to the vector afterwards.
    """

    name = "openai"
    #: Conservative. The documented ceiling is per-request tokens rather than a
    #: published input count, and a 10-K's chunks are not small.
    MAX_BATCH = 128

    def __init__(self, model: str = "text-embedding-3-large",
                 dim: int = EMBEDDING_DIM, *, api_key: str | None = None,
                 client: object | None = None) -> None:
        self.model, self.dim = model, dim
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - env dependent
            raise EmbeddingError(
                "OpenAIEmbedder needs the openai package — "
                "`pip install openai`"
            ) from exc
        if not self._api_key:
            raise EmbeddingError(
                "OPENAI_API_KEY is not set. Set it, or choose another "
                "provider with EMBEDDING_PROVIDER.")
        self._client = OpenAI(api_key=self._api_key)
        return self._client

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        out: list[list[float]] = []
        for batch in self._batched(texts, self.MAX_BATCH):
            response = client.embeddings.create(
                input=list(batch), model=self.model, dimensions=self.dim)
            # The API documents results in input order, but it also returns an
            # index on every item. Sorting by it costs nothing and removes the
            # need to trust that.
            out.extend(d.embedding
                       for d in sorted(response.data, key=lambda d: d.index))
        return self._check(out, len(texts))


PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "voyage": VoyageEmbedder,
    "openai": OpenAIEmbedder,
    "hashing": HashingEmbedder,
}


def get_provider(name: str, *, model: str | None = None,
                 dim: int | None = None, **kwargs) -> EmbeddingProvider:
    """Build a provider by name. This is the one config change."""
    try:
        cls = PROVIDERS[name.lower()]
    except KeyError:
        raise EmbeddingError(
            f"unknown embedding provider {name!r}. "
            f"Known: {', '.join(sorted(PROVIDERS))}") from None
    if cls is HashingEmbedder:
        return cls(dim=dim or EMBEDDING_DIM)
    opts = {k: v for k, v in (("model", model), ("dim", dim)) if v is not None}
    return cls(**opts, **kwargs)


def default_provider(**kwargs) -> EmbeddingProvider:
    """The provider named by `EMBEDDING_PROVIDER`.

    Raises when nothing is configured rather than falling back to
    `HashingEmbedder`. A search index quietly full of hash vectors answers
    every query with something plausible and wrong, and nothing about the
    system looks broken while it happens — so the failure is moved to startup,
    where it is visible.
    """
    name = os.environ.get("EMBEDDING_PROVIDER")
    if not name:
        raise EmbeddingError(
            "EMBEDDING_PROVIDER is not set. Choose one of: "
            f"{', '.join(sorted(PROVIDERS))}. Use 'hashing' only for tests — "
            "it has no semantics and will make retrieval look like it works.")
    return get_provider(
        name,
        model=os.environ.get("EMBEDDING_MODEL"),
        dim=int(os.environ["EMBEDDING_DIM"]) if os.environ.get("EMBEDDING_DIM")
        else None,
        **kwargs)
