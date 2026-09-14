"""Retrieval ranking, and the citation a hit carries.

Both are tested against the objects the live search endpoint actually uses.
They used to be tested against a parallel `Hit` class belonging to a retrieval
module that queried tables which no longer existed — so the ranking was covered
and the shipped ranking was not, which is worse than no test at all.
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date

from evident_db import Chunk
from evident_retrieval.rerank import rerank


@dataclass
class _Hit:
    """The two fields `rerank` needs, as `SearchHitOut` provides them."""
    chunk_id: str
    score: float
    filed_at: date


class Ranking(unittest.TestCase):
    def hit(self, score, when, cid):
        return _Hit(chunk_id=cid, score=score, filed_at=when)

    def test_newer_filing_wins_a_near_tie(self):
        """A 10-K and a stale 10-Q often carry the same sentence."""
        old = self.hit(0.90, date(2021, 1, 1), "old")
        new = self.hit(0.88, date(2025, 1, 1), "new")
        self.assertEqual([h.chunk_id for h in rerank([old, new])], ["new", "old"])

    def test_recency_does_not_override_a_clear_similarity_win(self):
        old = self.hit(0.95, date(2021, 1, 1), "old")
        new = self.hit(0.55, date(2025, 1, 1), "new")
        self.assertEqual(rerank([old, new])[0].chunk_id, "old")

    def test_zero_weight_ranks_purely_by_similarity(self):
        old = self.hit(0.90, date(2021, 1, 1), "old")
        new = self.hit(0.88, date(2025, 1, 1), "new")
        self.assertEqual([h.chunk_id for h in rerank([old, new], recency_weight=0)],
                         ["old", "new"])

    def test_same_day_filings_keep_similarity_order(self):
        # the span is zero; the guard against dividing by it is load-bearing
        a = self.hit(0.9, date(2025, 1, 1), "a")
        b = self.hit(0.5, date(2025, 1, 1), "b")
        self.assertEqual([h.chunk_id for h in rerank([a, b])], ["a", "b"])

    def test_empty_is_safe(self):
        self.assertEqual(rerank([]), [])


class Citation(unittest.TestCase):
    """The string a reader is shown to locate a quote in the filing."""

    def test_page_and_section(self):
        c = Chunk(page_number=87, section_title="Item 7")
        self.assertEqual(c.citation(), "p. 87 · Item 7")

    def test_missing_page_is_marked_rather_than_blank(self):
        # a citation that silently omits its location reads as if it had one
        self.assertEqual(Chunk(page_number=None, section_title="Item 7").citation(),
                         "— · Item 7")

    def test_page_alone(self):
        self.assertEqual(Chunk(page_number=87, section_title=None).citation(),
                         "p. 87")


if __name__ == "__main__":
    unittest.main(verbosity=2)
