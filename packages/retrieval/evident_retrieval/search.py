"""Vector search over evidence spans.

Retrieval here is a *supporting* index, not the product. A hit is only useful if
it can name the document, page and paragraphs it came from, so every result
carries that and the SQL joins back to `chunks` rather than returning a bare
score.

The ranking is deliberately hybrid: cosine similarity finds the passage, but
recency and form type decide which of five near-identical passages a reader
actually wants. A 10-K and a stale 10-Q often say the same sentence; the newer
one is almost always the right answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from .embed import Embedder


@dataclass(slots=True, frozen=True)
class Hit:
    chunk_id: str
    document_id: int
    accession: str
    form_type: str
    filed_date: date
    page_start: int | None
    page_end: int | None
    section_path: list[str]
    paragraph_ids: list[str]
    text: str
    score: float

    def citation(self) -> str:
        page = f"p. {self.page_start}" if self.page_start else "—"
        section = " › ".join(self.section_path) if self.section_path else ""
        return f"{self.form_type} · {page}{' · ' + section if section else ''}"


_SQL = """
select c.chunk_id, d.id, d.accession, d.form_type, d.filed_date,
       c.page_start, c.page_end,
       coalesce(s.path, '{}') as section_path,
       c.paragraph_ids, c.text,
       1 - (e.embedding <=> %(q)s::vector) as similarity
  from chunk_embeddings e
  join chunks    c on c.id = e.chunk_id
  join documents d on d.id = c.document_id
  left join sections s on s.id = c.section_id
 where e.provider = %(provider)s
   and e.model    = %(model)s
   and (%(cik)s is null or d.cik = %(cik)s)
   and (%(forms)s is null or d.form_type = any(%(forms)s))
 order by e.embedding <=> %(q)s::vector
 limit %(k)s
"""


def search(conn: Any, query: str, *, embedder: Embedder, cik: str | None = None,
           form_types: Sequence[str] | None = None, k: int = 20,
           recency_weight: float = 0.15) -> list[Hit]:
    """Nearest chunks, re-ranked so newer filings win near-ties."""
    from .store import _vector_literal

    vector = _vector_literal(embedder.embed([query]).vectors[0])
    params = {"q": vector, "provider": embedder.provider, "model": embedder.model,
              "cik": cik, "forms": list(form_types) if form_types else None,
              "k": k * 3}          # over-fetch so re-ranking has room to work
    with conn.cursor() as cur:
        cur.execute(_SQL, params)
        rows = cur.fetchall()

    hits = [Hit(chunk_id=r[0], document_id=r[1], accession=r[2], form_type=r[3],
                filed_date=r[4], page_start=r[5], page_end=r[6],
                section_path=list(r[7] or []), paragraph_ids=list(r[8] or []),
                text=r[9], score=float(r[10]))
            for r in rows]
    return rerank(hits, recency_weight=recency_weight)[:k]


def rerank(hits: Sequence[Hit], *, recency_weight: float = 0.15) -> list[Hit]:
    """Blend similarity with recency.

    Kept as a pure function so it is testable without a database — the ranking
    is a product decision and deserves a test, not a buried ORDER BY.
    """
    if not hits:
        return []
    dates = [h.filed_date.toordinal() for h in hits]
    lo, hi = min(dates), max(dates)
    span = (hi - lo) or 1

    def blended(h: Hit) -> float:
        recency = (h.filed_date.toordinal() - lo) / span
        return h.score * (1 - recency_weight) + recency * recency_weight

    return sorted(hits, key=blended, reverse=True)
