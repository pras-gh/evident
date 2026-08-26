"""Retrieval ranking.

The graph-building tests that used to live alongside these moved to
test_graph_contract.py when the graph gained a frozen API; what remains here is
the hybrid ranking, which is a product decision and deserves its own test
rather than a buried ORDER BY.
"""
from __future__ import annotations

import unittest
from datetime import date

from evident_retrieval.search import Hit, rerank


class Ranking(unittest.TestCase):
    def hit(self, score, when, cid):
        return Hit(chunk_id=cid, document_id=1, accession="a", form_type="10-K",
                   filed_date=when, page_start=87, page_end=87,
                   section_path=["Part II", "Item 7"], paragraph_ids=["p_a"],
                   text="…", score=score)

    def test_newer_filing_wins_a_near_tie(self):
        """A 10-K and a stale 10-Q often carry the same sentence."""
        old = self.hit(0.90, date(2021, 1, 1), "old")
        new = self.hit(0.88, date(2025, 1, 1), "new")
        self.assertEqual([h.chunk_id for h in rerank([old, new])], ["new", "old"])

    def test_recency_does_not_override_a_clear_similarity_win(self):
        old = self.hit(0.95, date(2021, 1, 1), "old")
        new = self.hit(0.55, date(2025, 1, 1), "new")
        self.assertEqual(rerank([old, new])[0].chunk_id, "old")

    def test_citation_is_readable(self):
        self.assertEqual(self.hit(1, date(2025, 1, 1), "c").citation(),
                         "10-K · p. 87 · Part II › Item 7")

    def test_empty_is_safe(self):
        self.assertEqual(rerank([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
