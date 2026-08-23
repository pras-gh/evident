"""Tests for the topic graph and retrieval ranking."""
from __future__ import annotations

import unittest
from datetime import date

from evident_graph.builder import build, slice_by_period
from evident_memory.entities import CompanyMemory, DocumentRef, Evidence, Topic
from evident_retrieval.search import Hit, rerank


def ev(doc):
    return Evidence(document_id=doc, paragraph_id="p_a", page_number=1, quote="…")


def memory():
    return CompanyMemory(
        company_id="0001045810", ticker="NVDA",
        documents=[DocumentRef("d1", "acc-1", "10-K", date(2023, 2, 1), ""),
                   DocumentRef("d2", "acc-2", "10-K", date(2024, 2, 1), ""),
                   DocumentRef("d3", "acc-3", "10-K", date(2025, 2, 1), "")],
        topics=[
            Topic("blackwell", "Blackwell", date(2024, 2, 1), date(2025, 2, 1),
                  [ev("d2"), ev("d3")]),
            Topic("cuda", "CUDA", date(2023, 2, 1), date(2025, 2, 1),
                  [ev("d1"), ev("d2"), ev("d3")]),
            Topic("gaming", "Gaming", date(2023, 2, 1), date(2023, 2, 1), [ev("d1")]),
        ])


class Graph(unittest.TestCase):
    def setUp(self):
        self.g = build(memory())

    def test_has_a_company_core_every_topic_hangs_off(self):
        core = [n for n in self.g.nodes if n.kind == "company"]
        self.assertEqual(len(core), 1)
        about = [e for e in self.g.edges if e.kind == "about"]
        self.assertEqual(len(about), 3)

    def test_topic_weight_is_mention_count(self):
        cuda = next(n for n in self.g.nodes if n.id == "topic:cuda")
        self.assertEqual(cuda.weight, 3)

    def test_co_occurrence_edges_name_their_documents(self):
        """An edge you cannot explain is decoration."""
        edge = next(e for e in self.g.edges
                    if e.kind == "co_occurs"
                    and {e.source, e.target} == {"topic:blackwell", "topic:cuda"})
        self.assertEqual(edge.weight, 2)
        self.assertEqual(sorted(edge.documents), ["d2", "d3"])

    def test_threshold_prunes_weak_relationships(self):
        strong = build(memory(), min_co_occurrence=2)
        pairs = {frozenset((e.source, e.target)) for e in strong.edges
                 if e.kind == "co_occurs"}
        self.assertIn(frozenset(("topic:blackwell", "topic:cuda")), pairs)
        # Gaming shares only d1 with CUDA, so it drops out at threshold 2
        self.assertNotIn(frozenset(("topic:cuda", "topic:gaming")), pairs)

    def test_slice_shows_the_graph_as_it_stood(self):
        """What the replay animation scrubs — topics that did not exist yet
        are genuinely absent, not greyed out."""
        early = slice_by_period(self.g, until=date(2023, 6, 1))
        ids = {n.id for n in early.nodes}
        self.assertIn("topic:cuda", ids)
        self.assertNotIn("topic:blackwell", ids)

    def test_slice_drops_edges_that_lost_an_endpoint(self):
        early = slice_by_period(self.g, until=date(2023, 6, 1))
        ids = {n.id for n in early.nodes}
        for e in early.edges:
            self.assertIn(e.source, ids)
            self.assertIn(e.target, ids)

    def test_serialises_for_the_client(self):
        js = self.g.to_json()
        self.assertEqual({"nodes", "edges"}, set(js))
        self.assertIn("firstSeen", js["nodes"][0])


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
