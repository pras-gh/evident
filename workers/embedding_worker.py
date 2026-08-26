"""Embedding worker — vectors for chunks that don't have one yet.

Runs independently of ingestion so a re-embed never forces a re-parse, and a
provider change is a backfill rather than a migration.

Two things it refuses to do:

  * Write a vector whose dimension does not match the column. A silent
    truncation or pad would poison the index in a way that only shows up as
    quietly worse search results.
  * Guess the provider. Provider and model are written next to every vector, so
    a mixed table stays interpretable and a backfill can target exactly the rows
    that need it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from evident_db import EMBEDDING_DIM, session_scope
from evident_db.repositories import chunks_without_embeddings, set_embeddings
from evident_retrieval.embed import Embedder, default_embedder

log = logging.getLogger("evident.embedding_worker")


@dataclass(slots=True)
class EmbedResult:
    embedded: int
    batches: int
    provider: str
    model: str


def run(*, url: str | None = None, embedder: Embedder | None = None,
        batch_size: int = 128, max_chunks: int | None = None,
        company_id: int | None = None) -> EmbedResult:
    embedder = embedder or default_embedder()
    if embedder.dim != EMBEDDING_DIM:
        raise ValueError(
            f"{embedder.provider}/{embedder.model} produces dim {embedder.dim}, "
            f"but chunks.embedding is vector({EMBEDDING_DIM}). Add a migration "
            f"for the new dimension rather than writing vectors that will not "
            f"index correctly."
        )

    embedded = batches = 0
    with session_scope(url) as db:
        while True:
            remaining = None if max_chunks is None else max_chunks - embedded
            if remaining is not None and remaining <= 0:
                break
            size = batch_size if remaining is None else min(batch_size, remaining)
            pending = chunks_without_embeddings(db, limit=size, company_id=company_id)
            if not pending:
                break

            result = embedder.embed([c.text for c in pending])
            if len(result.vectors) != len(pending):
                raise RuntimeError(
                    f"embedder returned {len(result.vectors)} vectors for "
                    f"{len(pending)} chunks — refusing to write a misaligned batch"
                )
            embedded += set_embeddings(
                db, zip((c.id for c in pending), result.vectors),
                provider=result.provider, model=result.model)
            batches += 1
            db.flush()
            log.info("embedded batch %d (%d chunks) via %s/%s",
                     batches, len(pending), result.provider, result.model)

    return EmbedResult(embedded, batches, embedder.provider, embedder.model)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    r = run()
    print(f"embedded {r.embedded} chunks in {r.batches} batches "
          f"via {r.provider}/{r.model}")
