"""Embedding providers — one interface, chosen by configuration.

    EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]

Four implementations. `VoyageEmbedder` is the first choice, because Voyage
publishes `voyage-finance-2` as tuned for finance retrieval, which is exactly
this corpus. `GeminiEmbedder` uses the Gemini key the AI gateway already has.
`OpenAIEmbedder` is the other alternative. `HashingEmbedder` is for tests and
is no longer reachable without asking for it by name.

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
import logging
import math
import os
import re
import time
from typing import Any, Callable, Sequence

log = logging.getLogger("parallx.embed")

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


class GeminiEmbedder(EmbeddingProvider):
    """Google Gemini, on `gemini-embedding-2`, with the key the AI gateway
    already uses (`GEMINI_API_KEY`), sent in a header and never in the URL.

    `outputDimensionality` asks for the shared width. Gemini Embedding 2 accepts
    128–3072 and normalises a shortened vector itself.

    **The task goes in the text.** gemini-embedding-2 takes no `taskType`;
    Google's docs say to write the task into the input instead:
    `task: search result | query: …` for a query and `title: none | text: …`
    for a document. gemini-embedding-001, chosen with EMBEDDING_MODEL, takes
    `taskType` and returns unnormalised vectors below 3072, so both are handled
    for it.

    **One text per request.** Several parts in one request make a single
    averaged vector, so each text is its own request inside
    `batchEmbedContents`.

    Not routed through the AI gateway, so not logged in `ai_usage_logs`; token
    counts are logged here.
    """

    name = "gemini"
    #: Texts per `batchEmbedContents` call. Google doesn't document a limit;
    #: 100 is conservative.
    MAX_BATCH = 100
    BASE_URL = "https://generativelanguage.googleapis.com"
    RETRYABLE = frozenset({429, 500, 502, 503, 504})

    def __init__(self, model: str = "gemini-embedding-2",
                 dim: int = EMBEDDING_DIM, *, api_key: str | None = None,
                 http: Any | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.model, self.dim = model, dim
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._base = os.environ.get("AI_GEMINI_BASE_URL") or self.BASE_URL
        self._http = http
        self._sleep = sleep

    @property
    def _legacy(self) -> bool:
        return self.model.startswith("gemini-embedding-001")

    def _request(self, text: str, *, query: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"model": f"models/{self.model}",
                                "outputDimensionality": self.dim}
        if self._legacy:
            body["taskType"] = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
        else:
            text = (f"task: search result | query: {text}" if query
                    else f"title: none | text: {text}")
        body["content"] = {"parts": [{"text": text}]}
        return body

    def _post(self, body: dict[str, Any], *, attempts: int, max_wait: float) -> dict:
        if not self._api_key:
            raise EmbeddingError(
                "GEMINI_API_KEY is not set. Set it, or choose another "
                "provider with EMBEDDING_PROVIDER.")
        import httpx
        http = self._http or httpx.Client(timeout=60)
        url = f"{self._base}/v1beta/models/{self.model}:batchEmbedContents"
        for attempt in range(1, attempts + 1):
            try:
                r = http.post(url, json=body, headers={"x-goog-api-key": self._api_key})
            except httpx.HTTPError as exc:
                status, message, hint = None, type(exc).__name__, None
            else:
                if r.status_code == 200:
                    return r.json()
                status, message, hint = r.status_code, *self._error(r)
            if (status is None or status in self.RETRYABLE) and attempt < attempts:
                wait = max(hint or 0.0, 2.0 ** (attempt - 1))
                if wait <= max_wait:
                    self._sleep(wait)
                    continue
            raise EmbeddingError(f"{self.name}/{self.model}: "
                                 f"{f'HTTP {status}' if status else 'request failed'}: "
                                 f"{message}")
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _error(r: Any) -> tuple[str, float | None]:
        try:
            err = r.json().get("error", {})
        except ValueError:
            return r.text[:300], None
        hint = None
        for detail in err.get("details") or []:
            m = re.fullmatch(r"([\d.]+)s", str(detail.get("retryDelay", "")))
            if m:
                hint = float(m.group(1))
        return str(err.get("message", ""))[:300], hint

    def _embed(self, texts: Sequence[str], *, query: bool, attempts: int,
               max_wait: float) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for batch in self._batched(texts, self.MAX_BATCH):
            data = self._post({"requests": [self._request(t, query=query) for t in batch]},
                              attempts=attempts, max_wait=max_wait)
            vectors = [e["values"] for e in data.get("embeddings", [])]
            if self._legacy:
                vectors = [_unit(v) for v in vectors]
            out.extend(vectors)
            log.info("gemini/%s embedded %d text(s), usage %s",
                     self.model, len(batch), data.get("usageMetadata"))
        return self._check(out, len(texts))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # a worker can wait out a rate limit or an overloaded model
        return self._embed(texts, query=False, attempts=6, max_wait=60.0)

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        # someone is waiting for search results: one quick retry at most
        return self._embed(texts, query=True, attempts=2, max_wait=2.0)


def _unit(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if norm else v


PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "voyage": VoyageEmbedder,
    "gemini": GeminiEmbedder,
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
