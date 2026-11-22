"""Importance scoring.

Produces the 0-100 `importance` in the graph contract.

An unexplainable score is the same failure as an uncited claim, so the score is
a weighted sum of four components that are stored alongside it. "Why is this
94?" has an answer, and the answer is data rather than a shrug.

    frequency   how often it is mentioned, log-scaled so a topic mentioned 400
                times does not flatten everything else to zero
    spread      how many distinct filings mention it — something discussed once
                at length matters less than something raised every quarter
    recency     how recently, relative to the newest filing; a topic last seen
                in 2019 is history, not strategy
    centrality  how connected it is; a topic that touches many others is
                structurally important even when mentioned less
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

WEIGHTS = {"frequency": 0.35, "spread": 0.25, "recency": 0.20, "centrality": 0.20}
RECENCY_HALF_LIFE_DAYS = 540          # ~18 months


@dataclass(slots=True)
class Signals:
    mentions: int = 0
    documents: int = 0
    last_seen_at: date | None = None
    degree: int = 0


@dataclass(slots=True, frozen=True)
class Score:
    importance: int
    components: dict[str, float] = field(default_factory=dict)


def _frequency(mentions: int, max_mentions: int) -> float:
    if max_mentions <= 0 or mentions <= 0:
        return 0.0
    return math.log1p(mentions) / math.log1p(max_mentions)


def _spread(documents: int, total_documents: int) -> float:
    if total_documents <= 0:
        return 0.0
    return min(1.0, documents / total_documents)


def _recency(last_seen: date | None, newest: date | None) -> float:
    if last_seen is None or newest is None:
        return 0.0
    days = max(0, (newest - last_seen).days)
    return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)


def _centrality(degree: int, max_degree: int) -> float:
    if max_degree <= 0:
        return 0.0
    return min(1.0, degree / max_degree)


def score(signals: Signals, *, max_mentions: int, total_documents: int,
          max_degree: int, newest_filing: date | None) -> Score:
    components = {
        "frequency": round(_frequency(signals.mentions, max_mentions), 4),
        "spread": round(_spread(signals.documents, total_documents), 4),
        "recency": round(_recency(signals.last_seen_at, newest_filing), 4),
        "centrality": round(_centrality(signals.degree, max_degree), 4),
    }
    total = sum(WEIGHTS[k] * v for k, v in components.items())
    return Score(importance=max(0, min(100, round(total * 100))),
                 components=components)


def score_all(signals: dict[str, Signals], *, total_documents: int,
              newest_filing: date | None) -> dict[str, Score]:
    """Score a whole company at once — the maxima are corpus-relative.

    Scoring an entity in isolation would make importance incomparable between
    companies *and* unstable as the corpus grows, which is not what a client
    holding a cached graph expects.
    """
    if not signals:
        return {}
    max_mentions = max((s.mentions for s in signals.values()), default=0)
    max_degree = max((s.degree for s in signals.values()), default=0)
    return {
        key: score(s, max_mentions=max_mentions, total_documents=total_documents,
                   max_degree=max_degree, newest_filing=newest_filing)
        for key, s in signals.items()
    }
