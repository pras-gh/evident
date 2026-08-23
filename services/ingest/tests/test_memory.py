"""Tests for layer 2 — company memory.

These cover the logic that turns per-document extractions into a durable model:
entity resolution, the promise lifecycle, and the timeline spine. The Claude
call itself is not exercised (it needs credentials) but its guardrail is,
because that guardrail is what makes "every claim is cited" true.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from elevate_ingest.memory.entities import (CompanyMemory, DocumentRef, Evidence,
                                            Person, Product, Promise, Risk, Role,
                                            Topic, normalise_metric,
                                            normalise_person, slugify)
from elevate_ingest.memory.extract import DropReport, drop_uncited
from elevate_ingest.memory.resolve import (ResolutionSignal, build_timeline,
                                           merge_metrics, merge_people,
                                           merge_products, merge_risks,
                                           merge_topics, resolve_promises)


def ev(doc="d1", pid="p_a", quote="…", when=None):
    return Evidence(document_id=doc, paragraph_id=pid, page_number=87,
                    quote=quote, observed_at=when)


class Normalisation(unittest.TestCase):
    def test_person_spellings_fold_together(self):
        variants = ["Mr. Jensen Huang", "JENSEN HUANG", "Huang, Jensen", "Jensen Huang"]
        self.assertEqual(len({normalise_person(v) for v in variants}), 1)

    def test_metric_labels_fold_to_one_series(self):
        variants = ["CapEx", "Capital Expenditure", "Capital expenditures"]
        self.assertEqual(len({normalise_metric(v) for v in variants}), 1)

    def test_distinct_people_stay_distinct(self):
        self.assertNotEqual(normalise_person("Jensen Huang"),
                            normalise_person("Colette Kress"))


class CitationGuardrail(unittest.TestCase):
    """A hallucinated citation looks exactly like a real one until clicked."""

    def test_invented_ids_are_dropped_not_stored(self):
        report = DropReport()
        kept = drop_uncited(
            [NS(paragraph_id="p_aaa"), NS(paragraph_id="p_INVENTED"),
             NS(paragraph_id=None)],
            {"p_aaa"}, report,
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(report.dropped, 2)
        self.assertIn("p_INVENTED", report.bad_ids)


class Resolution(unittest.TestCase):
    def test_topic_across_documents_becomes_one_topic(self):
        a = [Topic("blackwell", "Blackwell", date(2024, 3, 1), date(2024, 3, 1), [ev()])]
        b = [Topic("blackwell", "Blackwell", date(2025, 2, 1), date(2025, 2, 1), [ev("d2")])]
        merged = merge_topics([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].mention_count, 2)
        self.assertEqual(merged[0].first_seen_at, date(2024, 3, 1))
        self.assertEqual(merged[0].last_seen_at, date(2025, 2, 1))

    def test_person_keeps_fullest_spelling_and_all_roles(self):
        a = [Person("J. Huang", normalise_person("J Huang"), [Role("CEO")],
                    date(2022, 1, 1), date(2022, 1, 1), [ev()])]
        b = [Person("Jensen Huang", normalise_person("J Huang"), [Role("President")],
                    date(2025, 1, 1), date(2025, 1, 1), [ev("d2")])]
        merged = merge_people([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].full_name, "Jensen Huang")
        self.assertEqual(len(merged[0].roles), 2)

    def test_metric_becomes_a_series(self):
        rows = [
            ("Capital expenditures", "FY2024", 10922.0, "USD millions", ev(), date(2024, 9, 28)),
            ("CapEx",                "FY2025", 14602.0, "USD millions", ev("d2"), date(2025, 9, 27)),
        ]
        metrics = merge_metrics(rows)
        self.assertEqual(len(metrics), 1, "label drift split one series in two")
        self.assertEqual(metrics[0].series(),
                         [("FY2024", 10922.0), ("FY2025", 14602.0)])

    def test_restatement_is_flagged_not_deduplicated(self):
        rows = [
            ("Revenue", "FY2024", 100.0, "USD", ev("d1"), date(2024, 12, 31)),
            ("Revenue", "FY2024", 98.0,  "USD", ev("d2"), date(2024, 12, 31)),
        ]
        obs = merge_metrics(rows)[0].observations
        self.assertEqual(len(obs), 2, "a revised number is a finding, not a duplicate")
        self.assertTrue(obs[1].is_restated)

    def test_risk_absent_from_latest_filing_is_dropped_not_deleted(self):
        risks = merge_risks(
            [[Risk("china-export", "China export controls", None, "active",
                   date(2023, 2, 1), date(2024, 2, 1), [ev()])]],
            latest_filing_date=date(2025, 2, 1),
        )
        self.assertEqual(risks[0].status, "dropped")
        self.assertTrue(risks[0].evidence, "history must stay queryable")


class Promises(unittest.TestCase):
    """The entity a vector database cannot represent."""

    def setUp(self):
        self.promise = Promise(
            statement="Blackwell ships in volume in H2",
            made_at=date(2024, 3, 1), made_evidence=ev(quote="we expect volume in H2"),
            horizon="H2 2024", due_date=date(2024, 12, 31),
        )

    def test_evidence_backed_signal_resolves_it(self):
        sig = ResolutionSignal(
            statement=self.promise.statement, status="kept",
            evidence=ev("d2", "p_z", "shipped in volume during the fourth quarter"),
            resolved_at=date(2025, 2, 26), note="Confirmed in FY2025 10-K",
        )
        out = resolve_promises([self.promise], [sig], as_of=date(2025, 6, 1))[0]
        self.assertEqual(out.status, "kept")
        self.assertEqual(out.resolved_at, date(2025, 2, 26))
        self.assertIsNotNone(out.resolved_evidence)

    def test_silence_past_the_horizon_is_unclear_never_broken(self):
        out = resolve_promises([self.promise], [], as_of=date(2025, 6, 1))[0]
        self.assertEqual(out.status, "unclear")
        self.assertNotEqual(out.status, "broken")
        self.assertIn("not evidence of failure", out.resolution_note)

    def test_broken_requires_a_signal_carrying_evidence(self):
        sig = ResolutionSignal(
            statement=self.promise.statement, status="broken",
            evidence=ev("d2", "p_y", "the launch has been deferred to the following year"),
            resolved_at=date(2025, 2, 26),
        )
        out = resolve_promises([self.promise], [sig], as_of=date(2025, 6, 1))[0]
        self.assertEqual(out.status, "broken")
        self.assertIsNotNone(out.resolved_evidence)

    def test_not_yet_due_stays_open(self):
        out = resolve_promises([self.promise], [], as_of=date(2024, 6, 1))[0]
        self.assertEqual(out.status, "open")


class Timeline(unittest.TestCase):
    def test_spine_is_ordered_and_spans_every_entity_kind(self):
        mem = CompanyMemory(
            company_id="0000320193", ticker="AAPL",
            documents=[DocumentRef("d1", "0000-24-1", "10-K", date(2024, 2, 1), "", "FY2024")],
            topics=[Topic("blackwell", "Blackwell", date(2024, 3, 1), date(2025, 1, 1), [ev()])],
            products=[Product("Blackwell", "blackwell", "shipping", date(2024, 3, 1), date(2025, 1, 1), [ev()])],
            risks=[Risk("china-export", "China export controls", None, "active", date(2023, 2, 1), date(2024, 2, 1), [ev()])],
            promises=[Promise("Ships in H2", date(2024, 3, 1), ev(), due_date=date(2024, 12, 31))],
        )
        tl = build_timeline(mem)
        self.assertEqual([e.occurred_at for e in tl], sorted(e.occurred_at for e in tl))
        self.assertEqual({"filing", "topic", "product", "risk", "promise"},
                         {e.kind for e in tl})

    def test_memory_exposes_the_requested_shape(self):
        self.assertEqual(
            list(CompanyMemory(company_id="x").summary().keys()),
            ["documents", "timeline", "topics", "people", "metrics",
             "risks", "promises", "products", "events"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
