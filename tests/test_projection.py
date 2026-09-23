"""Memory cards projected on read from filings and entity mentions.

Each test is one way a card would mislead: a baseline announcing everything as
new, a 10-Q diffed against a 10-K, a filing with no extraction reading as every
risk dropped, an empty card that looks like a quiet one.
"""
from __future__ import annotations

import unittest
from datetime import date

from evident_memory.cards import CAPEX, GUIDANCE, LITIGATION, PRODUCTS, REVENUE, RISKS
from evident_memory.projection import Cited, CitedEvidence, Filing, project_cards

ITEM_1A = "Item 1A. Risk Factors"
K24 = Filing(1, "0001045810-24-000029", "10-K", date(2024, 2, 21), "FY2024")
Q1 = Filing(2, "0001045810-24-000124", "10-Q", date(2024, 5, 29), "Q1 FY2025")
K25 = Filing(3, "0001045810-25-000023", "10-K", date(2025, 2, 26), "FY2025")


def cite(name: str, filing: Filing, *, kind: str = "risk", section: str | None = ITEM_1A,
         pid: str = "20_1", n: int = 1) -> list[Cited]:
    slug = name.lower().replace(" ", "_")
    page, index = (int(x) for x in pid.split("#")[0].split("_"))
    return [Cited(slug, name, kind, filing.document_id, chunk_id=filing.document_id * 100 + i,
                  section_title=section, paragraph_id=f"{page + i}_{index}", page=page + i,
                  quote=f"{name} quote {i}")
            for i in range(n)]


class Risks(unittest.TestCase):
    def test_a_history_of_the_risk_factors_disclosed(self):
        cited = (cite("Export Controls", K24) + cite("Supply Chain", K24)
                 + cite("Export Controls", K25) + cite("Supply Chain", K25)
                 + cite("AI Diffusion Rule", K25))
        card = project_cards([K24, K25], cited).cards[RISKS]
        first, second = card.history
        self.assertEqual(first.summary,
                         "First on record: 2 risk factors — Export Controls, Supply Chain.")
        self.assertEqual(second.summary, "1 new risk factor: AI Diffusion Rule.")
        self.assertEqual(second.source_note, "FY2025 10-K · 0001045810-25-000023")
        self.assertEqual(second.as_of, date(2025, 2, 26))

    def test_a_dropped_risk_factor_is_reported(self):
        cited = cite("Export Controls", K24) + cite("DGX Cloud", K24) + cite("Export Controls", K25)
        card = project_cards([K24, K25], cited).cards[RISKS]
        self.assertEqual(card.current.summary, "1 no longer disclosed: DGX Cloud.")

    def test_10qs_are_not_diffed_against_10ks(self):
        """A 10-Q's risk factors cover changes since the 10-K. Diffed against
        it, every quarter would drop most of the list."""
        cited = (cite("Export Controls", K24) + cite("Supply Chain", K24)
                 + cite("Export Controls", Q1)
                 + cite("Export Controls", K25) + cite("Supply Chain", K25))
        card = project_cards([K24, Q1, K25], cited).cards[RISKS]
        self.assertEqual([r.source_note.split(" ·")[0] for r in card.history],
                         ["FY2024 10-K", "FY2025 10-K"])
        self.assertEqual(card.current.summary, "Restated without change.")

    def test_a_filing_with_nothing_extracted_adds_no_revision(self):
        """Not yet extracted is not 'every risk factor dropped'."""
        card = project_cards([K24, K25], cite("Export Controls", K24)).cards[RISKS]
        self.assertEqual(len(card.history), 1)

    def test_only_risk_entities_in_the_risk_section(self):
        cited = (cite("Export Controls", K24) + cite("China", K24, kind="geography")
                 + cite("Supply Chain", K24, section="Item 7. MD&A"))
        card = project_cards([K24], cited).cards[RISKS]
        self.assertEqual([f.label for f in card.current.facts], ["Export Controls"])

    def test_most_discussed_first_with_its_first_paragraph_as_evidence(self):
        cited = (cite("Supply Chain", K24, n=2)
                 + cite("Export Controls", K24, pid="30_1", n=4)
                 # later in the list, earlier in the filing
                 + [Cited("export_controls", "Export Controls", "risk", 1, 7, ITEM_1A,
                          "9_5", 9, "the earliest one")])
        facts = project_cards([K24], cited).cards[RISKS].current.facts
        self.assertEqual([f.label for f in facts], ["Export Controls", "Supply Chain"])
        ev = facts[0].evidence
        self.assertIsInstance(ev, CitedEvidence)
        self.assertEqual((ev.paragraph_id, ev.page_number, ev.quote, ev.chunk_id),
                         ("9_5", 9, "the earliest one", 7))
        self.assertEqual((ev.accession, ev.form_type, ev.entity_slug, ev.section_title),
                         (K24.accession, "10-K", "export_controls", ITEM_1A))


class Litigation(unittest.TestCase):
    def test_entities_cited_in_legal_proceedings(self):
        cited = (cite("Securities Class Action", K24, kind="company",
                      section="Item 3. Legal Proceedings")
                 + cite("Export Controls", K24))
        card = project_cards([K24], cited).cards[LITIGATION]
        self.assertEqual([f.label for f in card.current.facts], ["Securities Class Action"])
        self.assertEqual(card.current.summary,
                         "First on record: 1 matter — Securities Class Action.")


class Unavailable(unittest.TestCase):
    def test_cards_the_data_cannot_fill_say_why(self):
        projected = project_cards([K24], cite("Export Controls", K24))
        self.assertEqual(set(projected.unavailable), {REVENUE, CAPEX, PRODUCTS, GUIDANCE,
                                                      LITIGATION})
        self.assertIn("nothing extracts them", projected.unavailable[REVENUE])
        self.assertIn("earnings-call transcripts", projected.unavailable[PRODUCTS])
        self.assertIn("Legal Proceedings", projected.unavailable[LITIGATION])
        for kind in projected.unavailable:
            self.assertEqual(projected.cards[kind].history, [], kind)

    def test_with_no_annual_filing_the_risk_card_says_so(self):
        projected = project_cards([Q1], cite("Export Controls", Q1))
        self.assertEqual(projected.unavailable[RISKS], "No annual filings on record yet.")


if __name__ == "__main__":
    unittest.main()
