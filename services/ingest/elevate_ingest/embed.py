"""Embedding providers.

There is no embeddings endpoint in the Anthropic SDK, so this module does not
pick a provider for you. It defines the interface, ships a credential-free
default so the pipeline is runnable and testable end to end, and records
provider/model/dim next to every vector so a later migration is mechanical
rather than archaeological.
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    provider: str
    model: str
    dim: int
    vectors: list[list[float]]


class Embedder(Protocol):
    provider: str
    model: str
    dim: int

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...


class HashingEmbedder:
    """A deterministic bag-of-hashed-tokens vector. No network, no credentials.

    This exists so the pipeline can be run and tested without an account, and
    so CI has something to assert against. It carries no semantics — cosine
    similarity here reflects vocabulary overlap, nothing more. Swap it out
    before this serves a single real query.
    """

    provider = "local"
    model = "hashing-v1"

    def __init__(self, dim: int = 1536) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        vectors = [self._one(t) for t in texts]
        return EmbeddingBatch(self.provider, self.model, self.dim, vectors)

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in text.lower().split():
            h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


class HTTPEmbedder:
    """Adapter for any provider exposing a JSON `{input: [...]}` embeddings route.

    Deliberately generic: point it at whichever provider you settle on and set
    `provider`/`model`/`dim` to match. Kept out of the default path so nothing
    silently depends on an unconfigured service.
    """

    def __init__(self, *, provider: str, model: str, dim: int,
                 url: str, api_key_env: str = "EMBEDDINGS_API_KEY",
                 timeout: float = 30.0) -> None:
        self.provider, self.model, self.dim = provider, model, dim
        self.url, self.api_key_env, self.timeout = url, api_key_env, timeout

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        import json
        import urllib.request

        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"{self.api_key_env} is not set — refusing to write embeddings "
                f"that would silently be wrong."
            )
        body = json.dumps({"model": self.model, "input": list(texts)}).encode()
        req = urllib.request.Request(
            self.url, data=body,
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.load(resp)
        vectors = [row["embedding"] for row in payload["data"]]
        bad = [len(v) for v in vectors if len(v) != self.dim]
        if bad:
            raise ValueError(
                f"{self.provider}/{self.model} returned dim {bad[0]}, expected {self.dim}"
            )
        return EmbeddingBatch(self.provider, self.model, self.dim, vectors)


def default_embedder() -> Embedder:
    return HashingEmbedder()
