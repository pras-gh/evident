"""Tests for layer 3 — memory cards.

The property under test throughout is that a card is a *history*, not a value:
revisions are append-only, idempotent per filing, and each one carries a diff
that says what actually moved.
"""
from __future__ import annotations

import re
import sys
import unittest
from datetime import date
from pathlib import Path


from evident_memory.cards import (CAPEX, DEFAULT_SOURCES, GUIDANCE,
                                         LITIGATION, PRODUCTS, REVENUE, RISKS,
                                         CardFact, CardSource, build_cards,
                                         diff_facts, facts_for_guidance,
                                         facts_for_metric, facts_for_products,
                                         facts_for_risks, route)
from evident_memory.entities import (Evidence, Metric, Observation,
                                            Product, Promise, Risk)

SQL_SEED = Path(__file__).resolve().parents[1] / "db" / "migrations" / "003_memory_cards.sql"


def ev(pid="p_a", quote="…"):
    return Evidence(document_id="d1", paragraph_id=pid, page_number=87, quote=quote)


class Routing(unittest.TestCase):
    """The six bindings bind at three different granularities."""

    def test_form_type_binding(self):
        self.assertIn(REVENUE, route(form_type="10-K"))
        self.assertIn(REVENUE, route(form_type="10-Q"))
        self.assertNotIn(REVENUE, route(form_type="8-K"))

    def test_document_kind_binding(self):
        self.assertIn(PRODUCTS, route(doc_kind="earnings_call"))
        self.assertNotIn(PRODUCTS, route(doc_kind="10-K"))

    def test_speaker_role_binding(self):
        self.assertIn(GUIDANCE, route(doc_kind="earnings_call", speaker_role="CEO"))
        self.assertNotIn(GUIDANCE, route(doc_kind="earnings_call", speaker_role="CFO"))

    def test_section_bindings(self):
        self.assertIn(RISKS, route(section_title="Item 1A. Risk Factors"))
        self.assertIn(CAPEX, route(section_title="Consolidated Statements of Cash Flows"))
        self.assertIn(LITIGATION, route(section_title="Item 3. Legal Proceedings"))

    def test_one_filing_can_touch_several_cards(self):
        self.assertEqual(route(form_type="10-K", section_title="Item 1A. Risk Factors"),
                         [REVENUE, RISKS])

    def test_unspecified_predicate_means_dont_care(self):
        s = CardSource("x", "any 10-K", form_types=("10-K",))
        self.assertTrue(s.matches(form_type="10-K", section_title="anything"))
        self.assertFalse(s.matches(form_type="8-K"))


class SeedConsistency(unittest.TestCase):
    """Python routing and the SQL seed must not drift.

    A silent divergence means a filing stops updating a card and nobody notices
    until the card is visibly stale.
    """

    def test_kinds_and_labels_match_the_sql_seed(self):
        sql = SQL_SEED.read_text()
        block = sql.split("insert into card_sources")[1]
        pairs = set(re.findall(r"\('([a-z]+)',[^)]*?'([^']+)'\)", block))
        seeded = {(kind, label) for kind, label in pairs}
        in_python = {(s.card_kind, s.source_label) for s in DEFAULT_SOURCES}
        self.assertEqual(seeded, in_python)


class Diffing(unittest.TestCase):
    def test_detects_added_removed_and_changed(self):
        before = [CardFact("a", "Alpha", "1"), CardFact("b", "Beta", "2")]
        after = [CardFact("a", "Alpha", "9"), CardFact("c", "Gamma", "3")]
        d = diff_facts(before, after)
        self.assertEqual([f.label for f in d.added], ["Gamma"])
        self.assertEqual([f.label for f in d.removed], ["Beta"])
        self.assertEqual([(b.label, b.value, a.value) for b, a in d.changed],
                         [("Alpha", "1", "9")])
        self.assertTrue(d.is_material)

    def test_identical_facts_are_not_material(self):
        facts = [CardFact("a", "Alpha", "1")]
        self.assertFalse(diff_facts(facts, list(facts)).is_material)


class Revisions(unittest.TestCase):
    def setUp(self):
        self.card = build_cards()[CAPEX]

    def test_history_accumulates_and_current_is_the_newest(self):
        self.card.apply(as_of=date(2024, 2, 1), document_id="d1",
                        facts=[CardFact("capex:FY2024", "CapEx FY2024", "10,922")])
        self.card.apply(as_of=date(2025, 2, 1), document_id="d2",
                        facts=[CardFact("capex:FY2024", "CapEx FY2024", "10,922"),
                               CardFact("capex:FY2025", "CapEx FY2025", "14,602")])
        self.assertEqual(len(self.card.history), 2)
        self.assertEqual(self.card.current.revision, 2)
        self.assertEqual(self.card.current.as_of, date(2025, 2, 1))

    def test_second_revision_diffs_against_the_first(self):
        self.card.apply(as_of=date(2024, 2, 1), document_id="d1",
                        facts=[CardFact("capex:FY", "CapEx", "10,922")])
        rev = self.card.apply(as_of=date(2025, 2, 1), document_id="d2",
                              facts=[CardFact("capex:FY", "CapEx", "14,602")])
        self.assertTrue(rev.is_material)
        self.assertIn("rose from 10,922 to 14,602", rev.summary)

    def test_reingesting_a_filing_does_not_duplicate_history(self):
        self.card.apply(as_of=date(2024, 2, 1), document_id="d1",
                        facts=[CardFact("a", "A", "1")])
        again = self.card.apply(as_of=date(2024, 2, 1), document_id="d1",
                                facts=[CardFact("a", "A", "1")])
        self.assertIsNone(again)
        self.assertEqual(len(self.card.history), 1)

    def test_unchanged_restatement_is_recorded_but_not_material(self):
        self.card.apply(as_of=date(2024, 2, 1), document_id="d1",
                        facts=[CardFact("a", "A", "1")])
        rev = self.card.apply(as_of=date(2024, 5, 1), document_id="d2",
                              facts=[CardFact("a", "A", "1")])
        self.assertFalse(rev.is_material)
        self.assertEqual(rev.summary, "Restated without change.")
        self.assertEqual(len(self.card.history), 2)
        self.assertEqual(len(self.card.material_history), 1)

    def test_revision_carries_its_evidence(self):
        rev = self.card.apply(as_of=date(2024, 2, 1), document_id="d1",
                              facts=[CardFact("a", "A", "1", evidence=ev("p_x"))])
        self.assertEqual([e.paragraph_id for e in rev.evidence], ["p_x"])


class Projection(unittest.TestCase):
    def test_metric_card_is_one_fact_per_period(self):
        m = Metric("Capital expenditures", "capital expenditures", "USD millions",
                   [Observation("FY2024", 10922.0, "USD millions", ev()),
                    Observation("FY2025", 14602.0, "USD millions", ev())])
        facts = facts_for_metric([m], "CapEx")
        self.assertEqual([f.value for f in facts], ["10,922", "14,602"])

    def test_dropped_risk_leaves_the_fact_set_and_shows_as_removed(self):
        active = Risk("china", "China export controls", None, "active", evidence=[ev()])
        dropped = Risk("china", "China export controls", None, "dropped", evidence=[ev()])
        before = facts_for_risks([active])
        after = facts_for_risks([dropped])
        self.assertEqual(len(after), 0)
        d = diff_facts(before, after)
        self.assertEqual([f.label for f in d.removed], ["China export controls"])

    def test_guidance_reads_promises(self):
        p = Promise("Ships in H2", date(2024, 3, 1), ev(), horizon="H2 2024")
        facts = facts_for_guidance([p])
        self.assertEqual(facts[0].status, "open")
        self.assertEqual(facts[0].period, "H2 2024")

    def test_products_carry_lifecycle_status(self):
        facts = facts_for_products([Product("Blackwell", "blackwell", "shipping",
                                            evidence=[ev()])])
        self.assertEqual(facts[0].status, "shipping")


class ThreeFilings(unittest.TestCase):
    """The scenario the card exists for: watch one number move across years."""

    def test_capex_card_tells_a_story(self):
        card = build_cards()[CAPEX]
        series = [(date(2023, 2, 1), "d1", "8,100"),
                  (date(2024, 2, 1), "d2", "10,922"),
                  (date(2025, 2, 1), "d3", "14,602")]
        for as_of, doc, value in series:
            card.apply(as_of=as_of, document_id=doc,
                       facts=[CardFact("capex", "CapEx", value)],
                       source_note=f"10-K filed {as_of}")

        self.assertEqual(len(card.history), 3)
        self.assertEqual(len(card.material_history), 3)
        self.assertEqual([r.revision for r in card.history], [1, 2, 3])
        self.assertIn("rose from 10,922 to 14,602", card.current.summary)
        # the first revision is still queryable, which is the whole point
        self.assertEqual(card.history[0].facts[0].value, "8,100")


if __name__ == "__main__":
    unittest.main(verbosity=2)
