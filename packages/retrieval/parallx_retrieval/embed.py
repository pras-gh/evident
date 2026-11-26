"""Embedding providers — one interface, chosen by configuration.

    EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]

Four implementations. `VoyageEmbedder` is the first choice, on
`voyage-4-large` (`voyage-finance-2`, finance-tuned and 1024-only, by
`EMBEDDING_MODEL`). `GeminiEmbedder` uses the Gemini key the AI gateway already has.
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

import collections
import hashlib
import logging
import math
import os
import random
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


class EmbeddingAuthError(EmbeddingError):
    """The provider refused the credentials. Retrying will not help."""


class EmbeddingRateLimited(EmbeddingError):
    """Still rate limited after backing off. A smaller request may fit the
    per-minute token budget; the same one will not."""


class EmbeddingInvalidInput(EmbeddingError):
    """The provider rejected what was sent — a text over the context length,
    a request over the token limit. The same request will fail again; a
    smaller one, or one without the offending text, may not."""


class EmbeddingInvalidOutput(EmbeddingError):
    """A vector came back that cannot be stored or searched (non-finite, or
    all zeros). Its text is the suspect; the rest of the batch may be fine."""


#: List prices, USD per million input tokens, for estimating a run's cost.
#: Voyage's billing dashboard is the authority; `EMBEDDING_PRICE_PER_MTOK`
#: overrides. A model not listed has no estimate rather than a guessed one.
PRICE_PER_MTOK = {"voyage-4-large": 0.12}


def price_per_mtok(model: str) -> float | None:
    raw = os.environ.get("EMBEDDING_PRICE_PER_MTOK")
    return float(raw) if raw else PRICE_PER_MTOK.get(model)


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
    def _check(self, vectors: list[list[float]], expected: int, *,
               usable: bool = False) -> list[list[float]]:
        """The right count at the right width; with `usable`, also finite and
        not all zeros — what a real model returns, and what cosine distance
        needs."""
        if len(vectors) != expected:
            raise EmbeddingError(
                f"{self.name}: asked for {expected} vectors, got {len(vectors)}")
        for i, v in enumerate(vectors):
            if len(v) != self.dim:
                raise EmbeddingError(
                    f"{self.name}/{self.model}: vector {i} has {len(v)} "
                    f"dimensions, expected {self.dim}. The column is fixed "
                    "width; storing this would corrupt the index.")
            if usable and (not all(math.isfinite(x) for x in v) or not any(v)):
                raise EmbeddingInvalidOutput(
                    f"{self.name}/{self.model}: vector {i} is not a usable "
                    "embedding (non-finite, or all zeros); cosine distance to it "
                    "is undefined.")
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


class _Limiter:
    """Requests and tokens per minute, over a sliding minute. Waits before a
    request that would go over either; a request bigger than the whole token
    budget waits for an empty minute and goes (the provider then says no, and
    the caller makes it smaller)."""

    def __init__(self, rpm: int | None, tpm: int | None, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.rpm, self.tpm, self.clock, self.sleep = rpm, tpm, clock, sleep
        self.sent: collections.deque[tuple[float, int]] = collections.deque()

    def acquire(self, tokens: int) -> None:
        if not (self.rpm or self.tpm):
            return
        while True:
            now = self.clock()
            while self.sent and now - self.sent[0][0] >= 60:
                self.sent.popleft()
            used = sum(t for _, t in self.sent)
            over_rpm = self.rpm and len(self.sent) >= self.rpm
            over_tpm = self.tpm and self.sent and used + tokens > self.tpm
            if not (over_rpm or over_tpm):
                self.sent.append((now, tokens))
                return
            self.sleep(max(0.05, 60 - (now - self.sent[0][0])))

    def settle(self, tokens: int) -> None:
        """The last request's real token count, once the provider said it:
        the estimate (characters / 4) runs well over Voyage's count on filings."""
        if self.sent and tokens:
            self.sent[-1] = (self.sent[-1][0], tokens)


class VoyageEmbedder(EmbeddingProvider):
    """Voyage AI, on `voyage-4-large` at the shared 1024 dimensions, as floats.

    `voyage-finance-2` (by `EMBEDDING_MODEL`) is 1024-only, finance-tuned, and
    takes neither an output dimension nor a dtype. The voyage-3.5/4 family
    takes both, and is pinned to the column's width and to float — the only
    element type a pgvector `vector` column holds.

    Documents and queries are embedded with different `input_type`s, which
    Voyage retrieves measurably better for.

    **Nothing is cut short.** `truncation` is off: a text over the model's
    context is an error the caller sees (and records), not a vector of its
    first part.

    **Failures say what to do next** (`EmbeddingAuthError`,
    `EmbeddingRateLimited`, `EmbeddingInvalidInput`). Rate limits and server
    errors are retried with exponential backoff and jitter, honouring
    Retry-After; `VOYAGE_RPM` / `VOYAGE_TPM` pace requests ahead of the limit
    (an account without a payment method gets 3 requests and 10K tokens a
    minute). `tokens` and `requests` count what the API reported.
    """

    name = "voyage"
    #: The API accepts at most 1,000 texts per request.
    MAX_BATCH = 1000
    #: Tokens per request, for voyage-4-large and voyage-3-large; the smaller
    #: models allow more. The backfill packs requests under this.
    max_request_tokens = 120_000
    #: Models that are fixed-width and take no output_dimension/output_dtype.
    FIXED = frozenset({"voyage-finance-2", "voyage-law-2", "voyage-code-2",
                       "voyage-multilingual-2", "voyage-large-2", "voyage-large-2-instruct",
                       "voyage-2", "voyage-3", "voyage-3-lite"})
    RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

    def __init__(self, model: str = "voyage-4-large",
                 dim: int = EMBEDDING_DIM, *, api_key: str | None = None,
                 client: object | None = None, rpm: int | None = None,
                 tpm: int | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.model, self.dim = model, dim
        self._api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        self._client = client
        self._sleep = sleep
        env = lambda k: int(os.environ[k]) if os.environ.get(k) else None  # noqa: E731
        self._limiter = _Limiter(rpm or env("VOYAGE_RPM"), tpm or env("VOYAGE_TPM"),
                                 clock=clock, sleep=sleep)
        self.tokens = 0          # as the API reported them
        self.requests = 0
        self.retries = 0

    @property
    def request_budget(self) -> int:
        """Tokens one request may carry: the API's cap, or half the account's
        minute when that is smaller (3 requests and 10K tokens without a
        payment method). Half, because a request over the minute is refused
        however long it waits, and the batch estimate (characters / 4) runs
        under Voyage's count on figure-dense tables — about double on a
        10-K's financial statements, against 0.7 of it on prose."""
        if not self._limiter.tpm:
            return self.max_request_tokens
        return min(self.max_request_tokens, self._limiter.tpm // 2)

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
        # retries are ours: the SDK's would hide what was retried, and why
        self._client = voyageai.Client(api_key=self._api_key, max_retries=0, timeout=120)
        return self._client

    @staticmethod
    def _status(exc: Exception) -> int | None:
        status = getattr(exc, "http_status", None) or getattr(exc, "status_code", None)
        if status:
            return int(status)
        return {"AuthenticationError": 401, "RateLimitError": 429,
                "InvalidRequestError": 400, "MalformedRequestError": 400,
                "ServiceUnavailableError": 503, "ServerError": 500, "TryAgain": 503,
                "Timeout": 504, "APIConnectionError": 503}.get(type(exc).__name__)

    @staticmethod
    def _retry_after(exc: Exception) -> float | None:
        headers = getattr(exc, "headers", None) or {}
        try:
            raw = {k.lower(): v for k, v in dict(headers).items()}.get("retry-after")
            return float(raw) if raw else None
        except (TypeError, ValueError):
            return None

    def _call(self, batch: list[str], input_type: str, *, attempts: int,
              max_wait: float):
        kwargs: dict[str, Any] = {"texts": batch, "model": self.model,
                                  "input_type": input_type, "truncation": False}
        if self.model not in self.FIXED:
            kwargs["output_dimension"] = self.dim
            kwargs["output_dtype"] = "float"
        estimate = sum(len(t) // 4 + 1 for t in batch)
        for attempt in range(1, attempts + 1):
            self._limiter.acquire(estimate)
            try:
                response = self._get_client().embed(**kwargs)
            except EmbeddingError:
                raise
            except Exception as exc:  # the SDK's errors, classified by status
                status = self._status(exc)
                detail = f"{self.name}/{self.model}: HTTP {status or '?'}: {str(exc)[:300]}"
                if status in (401, 403):
                    raise EmbeddingAuthError(detail) from exc
                if status in (400, 413, 422):
                    raise EmbeddingInvalidInput(detail) from exc
                if status not in self.RETRY_STATUS:
                    raise EmbeddingError(detail) from exc
                wait = max(self._retry_after(exc) or 0.0,
                           2.0 ** (attempt - 1) * (1 + random.random() / 4))
                if attempt == attempts or wait > max_wait:
                    raise (EmbeddingRateLimited if status == 429 else EmbeddingError)(
                        f"{detail} (after {attempt} attempt{'s' if attempt > 1 else ''})"
                    ) from exc
                self.retries += 1
                log.warning("%s — retrying in %.1fs (attempt %d of %d)",
                            detail, wait, attempt + 1, attempts)
                self._sleep(wait)
                continue
            used = int(getattr(response, "total_tokens", 0) or 0)
            self.requests += 1
            self.tokens += used
            self._limiter.settle(used)
            return response.embeddings
        raise AssertionError("unreachable")  # pragma: no cover

    def _embed(self, texts: Sequence[str], input_type: str, *, attempts: int,
               max_wait: float) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for batch in self._batched(texts, self.MAX_BATCH):
            out.extend(self._call(list(batch), input_type, attempts=attempts,
                                  max_wait=max_wait))
        return self._check(out, len(texts), usable=True)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # a backfill can wait out a rate limit
        return self._embed(texts, "document", attempts=7, max_wait=90.0)

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        # someone is waiting for search results: one quick retry at most
        return self._embed(texts, "query", attempts=2, max_wait=3.0)


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
