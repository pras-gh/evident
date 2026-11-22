"""Ranking: blend similarity with recency.

A pure function, deliberately not a buried `ORDER BY`. Which of two similar
passages should win is a product decision — for filings, a near-tie should go
to the newer one, because a risk disclosed in 2019 and restated in 2025 is
mostly interesting in its 2025 form. That decision deserves a test.

This is the whole of what survived the old raw-SQL retrieval module. The rest
of it queried tables that no longer exist and had no callers; keeping an unused
second search implementation around is how someone later fixes a bug in the
wrong one.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol, Sequence, TypeVar


class Ranked(Protocol):
    score: float
    filed_at: date


T = TypeVar("T", bound=Ranked)


def rerank(hits: Sequence[T], *, recency_weight: float = 0.15) -> list[T]:
    """Re-order by similarity blended with how recent the filing is.

    `recency_weight` is the share of the final score recency can account for.
    At the default, a clear similarity win still beats a newer filing — recency
    only decides near-ties, which is the intent. Set it to 0 to rank purely by
    similarity.
    """
    if not hits:
        return []
    dates = [h.filed_at.toordinal() for h in hits]
    lo, hi = min(dates), max(dates)
    span = (hi - lo) or 1

    def blended(h: T) -> float:
        recency = (h.filed_at.toordinal() - lo) / span
        return h.score * (1 - recency_weight) + recency * recency_weight

    return sorted(hits, key=blended, reverse=True)
