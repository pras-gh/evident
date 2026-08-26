"""The Memory Graph API contract — frozen.

`GET /v1/company/{ticker}/graph` is a contract clients cache against. These
tests assert the exact shape rather than the implementation, so a refactor that
changes a field name fails here before it reaches anyone.

Additive fields are allowed. Renaming, removing or changing the meaning of
`id`, `label`, `type`, `importance`, `mentions`, `source`, `target`,
`relationship` or `strength` is a breaking change and should fail this file.
"""
from __future__ import annotations

import unittest
from datetime import date

from evident_graph.builder import EntityInput, build_graph, explain
from evident_graph.importance import Signals, score_all
from evident_graph.normalize import (canonical_label, display_label,
                                     entity_key, fold)
from evident_graph.relationships import TypedEdge, co_occurrence, strength

NODE_FIELDS = {"id", "label", "type", "importance", "mentions"}
EDGE_FIELDS = {"source", "target", "relationship", "strength"}


def sample():
    return [
        EntityInput("ai_infrastructure", "AI Infrastructure", "strategy",
                    {"d1", "d2", "d3", "d4"}, 67, date(2022, 2, 1), date(2025, 2, 26)),
        EntityInput("blackwell", "Blackwell", "product",
                    {"d3", "d4"}, 41, date(2024, 2, 21), date(2025, 2, 26)),
        EntityInput("export_controls", "Export Controls", "risk",
                    {"d2", "d3", "d4"}, 28, date(2023, 2, 1), date(2025, 2, 26)),
        EntityInput("gaming", "Gaming", "segment",
                    {"d1", "d2"}, 9, date(2022, 2, 1), date(2023, 2, 1)),
    ]


def graph():
    return build_graph(
        company="NVDA", entities=sample(),
        typed_edges=[TypedEdge("blackwell", "ai_infrastructure",
                               "drives_investment", "d4", 0.94, date(2025, 2, 26))],
        total_documents=4, newest_filing=date(2025, 2, 26)).to_contract()


class Contract(unittest.TestCase):
    def setUp(self):
        self.g = graph()

    def test_top_level_shape(self):
        self.assertEqual(set(self.g), {"company", "nodes", "edges"})
        self.assertEqual(self.g["company"], "NVDA")

    def test_every_node_has_exactly_the_contract_fields(self):
        for node in self.g["nodes"]:
            self.assertEqual(set(node), NODE_FIELDS, f"node shape drifted: {node}")

    def test_every_edge_has_exactly_the_contract_fields(self):
        for edge in self.g["edges"]:
            self.assertEqual(set(edge), EDGE_FIELDS, f"edge shape drifted: {edge}")

    def test_field_types(self):
        node = self.g["nodes"][0]
        self.assertIsInstance(node["id"], str)
        self.assertIsInstance(node["label"], str)
        self.assertIsInstance(node["type"], str)
        self.assertIsInstance(node["importance"], int)
        self.assertIsInstance(node["mentions"], int)
        edge = self.g["edges"][0]
        self.assertIsInstance(edge["strength"], float)

    def test_importance_is_0_to_100_and_strength_is_0_to_1(self):
        for n in self.g["nodes"]:
            self.assertGreaterEqual(n["importance"], 0)
            self.assertLessEqual(n["importance"], 100)
        for e in self.g["edges"]:
            self.assertGreaterEqual(e["strength"], 0.0)
            self.assertLessEqual(e["strength"], 1.0)

    def test_node_ids_are_stable_keys_not_surrogate_ids(self):
        """Clients cache these. A database id would change on rebuild."""
        ids = [n["id"] for n in self.g["nodes"]]
        self.assertIn("ai_infrastructure", ids)
        self.assertTrue(all(not i.isdigit() for i in ids))

    def test_every_edge_endpoint_is_a_node_in_the_response(self):
        ids = {n["id"] for n in self.g["nodes"]}
        for e in self.g["edges"]:
            self.assertIn(e["source"], ids)
            self.assertIn(e["target"], ids)

    def test_nodes_are_ordered_by_importance(self):
        scores = [n["importance"] for n in self.g["nodes"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_typed_relationship_survives_to_the_response(self):
        rels = {e["relationship"] for e in self.g["edges"]}
        self.assertIn("drives_investment", rels)

    def test_is_json_serialisable(self):
        import json
        json.loads(json.dumps(self.g))


class Normalization(unittest.TestCase):
    def test_variants_fold_to_one_key(self):
        """Without folding the graph shows three weak nodes instead of one
        strong one, and importance — mostly frequency — under-reports all of them."""
        for label in ("AI Infrastructure", "A.I. infrastructure",
                      "artificial intelligence infrastructure",
                      "Accelerated Computing"):
            self.assertEqual(entity_key(label), "ai_infrastructure")

    def test_keys_use_underscores_to_match_the_contract(self):
        self.assertEqual(entity_key("Export Controls"), "export_controls")
        self.assertNotIn("-", entity_key("China export restrictions"))

    def test_distinct_things_stay_distinct(self):
        self.assertNotEqual(entity_key("Blackwell"), entity_key("Gaming"))

    def test_fold_reports_the_grouping(self):
        grouped = fold(["AI Infrastructure", "Accelerated Computing", "Blackwell"])
        self.assertEqual(len(grouped["ai_infrastructure"]), 2)

    def test_label_keeps_its_casing(self):
        self.assertEqual(canonical_label("  AI   Infrastructure "),
                         "AI Infrastructure")

    def test_folded_entity_has_a_fixed_display_label(self):
        """Otherwise the name a reader sees depends on ingest order — whichever
        variant happened to land last."""
        for variant in ("Accelerated Computing", "A.I. infrastructure",
                        "artificial intelligence infrastructure"):
            key = entity_key(variant)
            self.assertEqual(display_label(key, variant), "AI Infrastructure")

    def test_unaliased_labels_pass_through(self):
        self.assertEqual(display_label(entity_key("Blackwell"), "Blackwell"),
                         "Blackwell")


class Importance(unittest.TestCase):
    def test_more_mentions_scores_higher_all_else_equal(self):
        s = score_all({"a": Signals(100, 5, date(2025, 1, 1), 3),
                       "b": Signals(3, 5, date(2025, 1, 1), 3)},
                      total_documents=5, newest_filing=date(2025, 1, 1))
        self.assertGreater(s["a"].importance, s["b"].importance)

    def test_stale_topics_score_lower(self):
        """Something last mentioned in 2019 is history, not strategy."""
        s = score_all({"recent": Signals(20, 3, date(2025, 1, 1), 2),
                       "stale": Signals(20, 3, date(2019, 1, 1), 2)},
                      total_documents=4, newest_filing=date(2025, 1, 1))
        self.assertGreater(s["recent"].importance, s["stale"].importance)

    def test_score_is_explainable(self):
        """A score nobody can interrogate is the same failure as an uncited claim."""
        out = explain(sample(), "ai_infrastructure", total_documents=4,
                      newest_filing=date(2025, 2, 26))
        self.assertEqual(set(out["components"]),
                         {"frequency", "spread", "recency", "centrality"})
        self.assertEqual(set(out["signals"]),
                         {"mentions", "documents", "last_seen_at", "degree"})

    def test_empty_corpus_does_not_divide_by_zero(self):
        self.assertEqual(score_all({}, total_documents=0, newest_filing=None), {})


class Relationships(unittest.TestCase):
    def test_co_occurrence_weight_is_shared_documents(self):
        edges = co_occurrence({"a": {"d1", "d2"}, "b": {"d2", "d3"}})
        self.assertEqual(edges[0].weight, 1)
        self.assertEqual(edges[0].documents, ("d2",))

    def test_min_shared_prunes_weak_edges(self):
        m = {"a": {"d1", "d2"}, "b": {"d2"}, "c": {"d1", "d2"}}
        self.assertEqual(len(co_occurrence(m, min_shared=2)), 1)

    def test_strength_is_relative_to_the_strongest_edge(self):
        edges = co_occurrence({"a": {"d1", "d2"}, "b": {"d1", "d2"}, "c": {"d1"}})
        mx = max(e.weight for e in edges)
        self.assertEqual(strength(edges[0], max_weight=mx), 1.0)

    def test_undirected_edges_are_stored_one_way_round(self):
        a = co_occurrence({"z": {"d1"}, "a": {"d1"}})[0].normalised()
        self.assertEqual((a.source, a.target), ("a", "z"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
