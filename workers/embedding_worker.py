"""Embedding worker — vectors for chunks the configured model has not embedded.

Runs independently of ingestion so a re-embed never forces a re-parse, and a
provider change is a backfill rather than a migration. `tools/generate_embeddings.py`
is its command line; the sync worker and on-demand fetches call `run` too.

What a run does:

* **Plans** (`repositories.embedding_plan`): the chunks with text in scope,
  how many the provider/model already embedded, and the ids left, newest
  filing first. Empty chunks are never sent. `force` re-embeds the lot.
* **Batches** by count (`batch_size`, and the provider's own ceiling) and by
  estimated tokens (under the provider's per-request limit, with headroom:
  the chunker's estimate is characters / 4, the provider counts its own).
* **Commits every batch.** A run that stops — a crash, a spent quota, Ctrl-C —
  keeps everything before it, and the next run continues with what is left:
  the plan is recomputed from the table, so nothing is embedded twice and no
  row is ever inserted, only updated in place.
* **Isolates bad input.** A batch the provider rejects (a text over its
  context; a request over its token limit) or still rate-limits after
  backing off is halved and retried, down to one chunk; a chunk rejected on
  its own is recorded as failed and the run goes on. Credentials refused, or
  a single chunk still rate-limited, stop the run (`EmbeddingStopped`, which
  carries the result so far).

Two things it refuses to do:

  * Write a vector whose dimension does not match the column, or that is not
    a usable vector (non-finite, all zeros). A silent truncation or pad would
    poison the index in a way that only shows up as quietly worse results.
  * Guess the provider. Provider and model are written next to every vector,
    so a mixed table stays interpretable and a backfill can target exactly
    the rows that need it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from parallx_db import EMBEDDING_DIM, session_scope
from parallx_db.repositories import chunks_by_id, embedding_plan, set_embeddings
from parallx_retrieval.embed import (EmbeddingAuthError, EmbeddingError,
                                     EmbeddingInvalidInput, EmbeddingInvalidOutput,
                                     EmbeddingProvider, EmbeddingRateLimited,
                                     default_provider, price_per_mtok)

log = logging.getLogger("parallx.embedding_worker")

#: Share of a provider's per-request token limit a batch is packed to: the
#: estimate (characters / 4) runs under real tokenizer counts on filings.
TOKEN_HEADROOM = 0.6


@dataclass(slots=True)
class EmbedResult:
    provider: str
    model: str
    #: chunks in scope with text
    eligible: int = 0
    #: ...without text, never sent
    empty: int = 0
    #: ...already embedded by this provider and model when the run began
    already: int = 0
    #: ...still to embed when the run began
    remaining: int = 0
    #: ...of those, this run's share (`max_chunks`)
    planned: int = 0
    embedded: int = 0
    failed: int = 0
    failed_ids: list[int] = field(default_factory=list)
    batches: int = 0
    #: as the provider reported them; else the chunker's estimate
    tokens: int = 0
    tokens_reported: bool = False
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    #: why the run stopped early, if it did
    stopped: str | None = None

    @property
    def left(self) -> int:
        """Still to embed after this run."""
        return self.remaining - self.embedded

    @property
    def cost_usd(self) -> float | None:
        price = price_per_mtok(self.model) if self.tokens_reported else None
        return None if price is None else round(self.tokens / 1e6 * price, 6)


class EmbeddingStopped(EmbeddingError):
    """The run stopped before finishing. Everything before it is kept; run
    again to continue. `result` is what it got done."""

    def __init__(self, message: str, result: EmbedResult) -> None:
        super().__init__(message)
        self.result = result


class _Stop(Exception):
    pass


def run(*, url: str | None = None, embedder: EmbeddingProvider | None = None,
        batch_size: int = 128, max_chunks: int | None = None,
        company_id: int | None = None, ticker: str | None = None,
        force: bool = False, dry_run: bool = False,
        progress: Callable[[EmbedResult], None] | None = None) -> EmbedResult:
    # wall time: a run that spans a laptop's sleep took that long to whoever waited
    started = time.time()
    embedder = embedder or default_provider()
    if embedder.dim != EMBEDDING_DIM:
        raise ValueError(
            f"{embedder.name}/{embedder.model} produces dim {embedder.dim}, "
            f"but chunks.embedding is vector({EMBEDDING_DIM}). Add a migration "
            f"for the new dimension rather than writing vectors that will not "
            f"index correctly."
        )
    result = EmbedResult(embedder.name, embedder.model)
    with session_scope(url) as db:
        counts, todo = embedding_plan(db, provider=embedder.name, model=embedder.model,
                                      company_id=company_id, ticker=ticker, force=force)
    result.eligible, result.empty, result.already = (
        counts["eligible"], counts["empty"], counts["already"])
    result.remaining = len(todo)
    todo = todo[:max_chunks] if max_chunks is not None else todo
    result.planned = len(todo)
    if dry_run or not todo:
        result.seconds = round(time.time() - started, 2)
        return result

    size = max(1, min(batch_size, getattr(embedder, "MAX_BATCH", batch_size)))
    limit = getattr(embedder, "request_budget", None) or getattr(
        embedder, "max_request_tokens", None)
    budget = [int(limit * TOKEN_HEADROOM) if limit else None]
    tokens_before = getattr(embedder, "tokens", None)

    def write(db, chunks, vectors) -> None:
        result.embedded += set_embeddings(db, zip((c.id for c in chunks), vectors),
                                          provider=embedder.name, model=embedder.model)
        db.commit()
        result.batches += 1
        if tokens_before is None:
            result.tokens += sum(c.token_estimate or 0 for c in chunks)
        else:
            result.tokens = getattr(embedder, "tokens", 0) - tokens_before
            result.tokens_reported = True

    def attempt(db, chunks) -> None:
        """Embed and write `chunks`; on rejection, halve and retry, down to the
        one chunk at fault, which is recorded and skipped."""
        try:
            vectors = embedder.embed([c.text for c in chunks])
        except EmbeddingAuthError as exc:
            result.errors.append(str(exc))
            raise _Stop(f"credentials refused: {exc}") from exc
        except (EmbeddingInvalidInput, EmbeddingInvalidOutput, EmbeddingRateLimited) as exc:
            result.errors.append(str(exc))
            limited = isinstance(exc, EmbeddingRateLimited)
            if len(chunks) > 1:
                if limited and budget[0]:
                    # the minute's token budget is smaller than a batch: smaller ones from here
                    budget[0] = max(1000, budget[0] // 2)
                half = len(chunks) // 2
                log.warning("batch of %d chunks: %s; retrying as two", len(chunks),
                            type(exc).__name__)
                attempt(db, chunks[:half])
                attempt(db, chunks[half:])
                return
            if limited:
                raise _Stop(f"still rate limited: {exc}") from exc
            result.failed += 1
            result.failed_ids.append(chunks[0].id)
            log.warning("chunk %s failed: %s", chunks[0].id, str(exc)[:200])
            return
        except EmbeddingError as exc:
            # a server error after retries, a missing key: halving will not help
            result.errors.append(str(exc))
            raise _Stop(str(exc)) from exc
        write(db, chunks, vectors)

    try:
        with session_scope(url) as db:
            for i in range(0, len(todo), size):
                group = chunks_by_id(db, todo[i:i + size])
                for batch in _packed(group, budget[0]):
                    attempt(db, batch)
                log.info("embedded %d of %d via %s/%s", result.embedded, result.planned,
                         embedder.name, embedder.model)
                if progress:
                    progress(result)
    except _Stop as stop:
        result.stopped = str(stop)
    finally:
        if tokens_before is not None:
            result.tokens = getattr(embedder, "tokens", 0) - tokens_before
            result.tokens_reported = True
        result.seconds = round(time.time() - started, 2)
    if result.stopped:
        raise EmbeddingStopped(result.stopped, result)
    return result


def _packed(chunks, budget: int | None):
    """`chunks` in consecutive batches of at most `budget` estimated tokens."""
    if not budget:
        if chunks:
            yield chunks
        return
    batch, used = [], 0
    for c in chunks:
        need = c.token_estimate or max(1, len(c.text) // 4)
        if batch and used + need > budget:
            yield batch
            batch, used = [], 0
        batch.append(c)
        used += need
    if batch:
        yield batch


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    r = run()
    print(f"embedded {r.embedded} chunks in {r.batches} batches "
          f"via {r.provider}/{r.model}")
