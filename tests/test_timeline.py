"""Timeline engine, pure: filings and mentions in, events out.

The rules under test, each one a way a timeline goes wrong:

* the earliest filing of each form is a baseline — nothing in it is "new";
* filings are compared with the previous filing of the **same form**, so a 10-Q
  between two 10-Ks does not make half the risk factors vanish and reappear;
* a change must be large in both absolute and relative terms to be reported;
* paragraphs are counted once each, however the chunker cut them;
* every change points at a paragraph, and the one it points at is the first
  in reading order — preferring one that is new this year.
"""
from __future__ import annotations

import unittest
from datetime import date

from evident_graph.timeline import (CARRIED_OVER, MIN_DELTA, MIN_RATIO, Filing,
                                    Mention, Topic, build_timeline, expanded,
                                    narrowed, overlap, shingles)

K24 = Filing(1, "0000000001-24-000001", "10-K", date(2024, 2, 21), "FY2024", 85)
Q1 = Filing(2, "0000000001-24-000002", "10-Q", date(2024, 5, 29), "Q1 FY2025")
K25 = Filing(3, "0000000001-25-000001", "10-K", date(2025, 2, 26), "FY2025", 87)
Q2 = Filing(4, "0000000001-24-000003", "10-Q", date(2024, 8, 28), "Q2 FY2025")
K26 = Filing(5, "0000000001-26-000001", "10-K", date(2026, 2, 25), "FY2026", 85)

EXPORT = Topic(10, "export_controls", "Export Controls", "risk")
CHINA = Topic(11, "china", "China", "geography")
H20 = Topic(12, "h20", "H20", "product")
DGX = Topic(13, "dgx_cloud", "DGX Cloud", "product")
TOPICS = [EXPORT, CHINA, H20, DGX]


def cite(topic: Topic, filing: Filing, n: int, *, first_page: int = 20,
         text: str | None = None) -> list[Mention]:
    """`n` distinct paragraphs of `filing` citing `topic`."""
    return [Mention(topic.entity_id, filing.document_id, chunk_id=filing.document_id * 1000 + i,
                    paragraph_id=f"{first_page + i}_1", page=first_page + i,
                    quote=f"{topic.name} sentence {i}",
                    text=None if text is None else f"{text} {i}")
            for i in range(n)]


def kinds(timeline, *, filing: Filing | None = None) -> dict[str, str]:
    return {e.topic.slug: e.kind for e in timeline.events
            if e.topic and (filing is None or e.filing == filing)}


class Baseline(unittest.TestCase):
    def test_one_filing_says_nothing_is_new(self):
        """With one filing loaded, every entity in it would "first appear" the
        day it was filed — the confident mistake this design exists to avoid."""
        t = build_timeline([K25], TOPICS, cite(EXPORT, K25, 20) + cite(H20, K25, 4))
        self.assertEqual([e.kind for e in t.events], ["filed"])
        self.assertEqual(t.coverage, {K25.accession: "baseline"})

    def test_the_earliest_of_each_form_is_a_baseline(self):
        t = build_timeline([K24, Q1, K25], TOPICS,
                           cite(EXPORT, K24, 13) + cite(EXPORT, Q1, 2) + cite(EXPORT, K25, 20))
        self.assertEqual(t.coverage, {K24.accession: "baseline",
                                      Q1.accession: "baseline",
                                      K25.accession: "compared"})
        self.assertEqual(kinds(t), {"export_controls": "expanded"})

    def test_every_filing_gets_a_filed_event_newest_first(self):
        t = build_timeline([K24, K26, K25], [], [])
        self.assertEqual([(e.kind, e.filing) for e in t.events],
                         [("filed", K26), ("filed", K25), ("filed", K24)])
        self.assertEqual(t.events[0].title, "FY2026 10-K Filed")
        self.assertEqual(t.events[0].category, "Filing")
        self.assertEqual(t.events[0].summary, "Form 10-K filed Feb 25, 2026, 85 pages.")
        self.assertIsNone(t.events[0].evidence)


class SameForm(unittest.TestCase):
    def test_a_10q_between_10ks_is_not_compared_with_them(self):
        """The 10-Q names export controls in 2 paragraphs; the 10-Ks in 13 and
        14. Compared across forms that is narrowed then expanded — two events
        about nothing. Compared within form, it is no change at all."""
        t = build_timeline([K24, Q1, K25], TOPICS,
                           cite(EXPORT, K24, 13) + cite(EXPORT, Q1, 2) + cite(EXPORT, K25, 14))
        self.assertEqual([e.kind for e in t.events], ["filed"] * 3)
        self.assertEqual(t.coverage[K25.accession], "compared")

    def test_10qs_are_compared_with_each_other(self):
        t = build_timeline([Q1, Q2], TOPICS, cite(CHINA, Q1, 3) + cite(CHINA, Q2, 9))
        [change] = [e for e in t.events if e.topic]
        self.assertEqual((change.kind, change.filing, change.compared_with),
                         ("expanded", Q2, Q1))

    def test_absence_from_a_10q_is_not_a_drop(self):
        """A 10-Q's risk factors describe changes since the 10-K; not naming
        something is not dropping it."""
        t = build_timeline([Q1, Q2], TOPICS, cite(CHINA, Q1, 3))
        self.assertEqual(kinds(t), {})

    def test_absence_from_a_10k_is(self):
        t = build_timeline([K24, K25], TOPICS, cite(DGX, K24, 3) + cite(EXPORT, K25, 1))
        dropped = [e for e in t.events if e.kind == "no_longer_disclosed"]
        self.assertEqual([e.topic for e in dropped], [DGX])
        self.assertEqual(dropped[0].title, "DGX Cloud No Longer Disclosed")
        self.assertEqual(dropped[0].summary,
                         "Cited in 3 paragraphs of the FY2024 10-K; in none of this filing.")

    def test_amendments_and_8ks_are_listed_but_never_compared(self):
        amended = Filing(6, "0000000001-25-000009", "10-K/A", date(2025, 4, 1), "FY2025")
        eight_k = Filing(7, "0000000001-25-000010", "8-K", date(2025, 5, 1))
        # the amendment restates Part III only: no risk factors at all
        t = build_timeline([K24, K25, amended, eight_k], TOPICS,
                           cite(EXPORT, K24, 13) + cite(EXPORT, K25, 13))
        self.assertEqual(t.coverage[amended.accession], "not compared")
        self.assertEqual(t.coverage[eight_k.accession], "not compared")
        self.assertEqual(kinds(t), {}, "the amendment made export controls vanish")
        self.assertEqual(sum(e.kind == "filed" for e in t.events), 4)


class Thresholds(unittest.TestCase):
    def test_both_the_floor_and_the_ratio_must_be_met(self):
        self.assertEqual((MIN_DELTA, MIN_RATIO), (3, 1.25))
        cases = {
            (13, 20): True,    # NVIDIA export controls, FY2024 → FY2025
            (13, 17): True,    # China: +4, ×1.31
            (2, 7): True,      # tariffs
            (1, 2): False,     # doubled, but by one paragraph
            (4, 6): False,     # +2: below the floor
            (40, 43): False,   # +3, but ×1.075: noise at this size
            (12, 15): True,    # exactly ×1.25 and exactly +3
            (13, 16): False,   # +3 but ×1.23
            (0, 5): False,     # from zero is "newly disclosed", not expanded
        }
        for (before, after), want in cases.items():
            with self.subTest(before=before, after=after):
                self.assertIs(expanded(before, after), want)

    def test_narrowed_mirrors_expanded(self):
        self.assertTrue(narrowed(10, 2))       # AI Diffusion Rule, FY2025 → FY2026
        self.assertTrue(narrowed(15, 12))
        self.assertFalse(narrowed(6, 4))       # Israel: −2
        self.assertFalse(narrowed(43, 40))
        self.assertFalse(narrowed(4, 0), "to zero is 'no longer disclosed'")

    def test_a_small_change_is_not_an_event(self):
        t = build_timeline([K24, K25], TOPICS, cite(CHINA, K24, 6) + cite(CHINA, K25, 4))
        self.assertEqual(kinds(t), {})


class Counting(unittest.TestCase):
    def test_sentence_split_parts_are_one_paragraph(self):
        """`20_1` and `20_1#2` are one oversized paragraph the chunker split;
        counting both would inflate every long paragraph."""
        parts = [Mention(CHINA.entity_id, K25.document_id, 3001, pid, 20)
                 for pid in ("20_1", "20_1#2", "20_1#3", "21_1")]
        t = build_timeline([K24, K25], TOPICS, cite(CHINA, K24, 1) + parts)
        self.assertEqual(kinds(t), {}, "1 → 2 paragraphs, not 1 → 4")

    def test_a_paragraph_in_two_overlapping_chunks_counts_once(self):
        """Consecutive chunks share a paragraph, and each chunk records its own
        mention of it. Counted per mention, 3 → 3 would read as 3 → 6."""
        dup = [Mention(CHINA.entity_id, K25.document_id, cid, f"{20 + i}_1", 20 + i)
               for i in range(3) for cid in (100 + i, 101 + i)]
        t = build_timeline([K24, K25], TOPICS, cite(CHINA, K24, 3) + dup)
        self.assertEqual(kinds(t), {})

    def test_mentions_of_other_documents_and_unknown_entities_are_ignored(self):
        stray = [Mention(CHINA.entity_id, 999, 1, "20_1", 20),
                 Mention(999, K25.document_id, 1, "20_1", 20)]
        t = build_timeline([K24, K25], TOPICS, stray)
        self.assertEqual(kinds(t), {})


class Evidence(unittest.TestCase):
    def test_newly_disclosed_points_at_its_first_paragraph_in_reading_order(self):
        """Input order is arbitrary; page 9 comes before page 10 even though
        "10_2" sorts before "9_5" as a string."""
        ms = [Mention(H20.entity_id, K26.document_id, 7, "10_2", 10, "second"),
              Mention(H20.entity_id, K26.document_id, 6, "9_5", 9, "first"),
              Mention(H20.entity_id, K26.document_id, 8, "31_1", 31, "third")]
        t = build_timeline([K25, K26], TOPICS, cite(EXPORT, K25, 1) + ms)
        [new] = [e for e in t.events if e.kind == "newly_disclosed"]
        self.assertEqual((new.evidence.paragraph_id, new.evidence.page, new.evidence.quote),
                         ("9_5", 9, "first"))
        self.assertEqual(new.title, "H20 Newly Disclosed")
        self.assertEqual(new.category, "Product")
        self.assertEqual(new.summary, "Cited in 3 paragraphs; in no earlier filing on record.")

    def test_expanded_prefers_a_paragraph_that_is_new_this_year(self):
        """The first paragraph citing export controls is carried over verbatim
        from last year; it shows nothing about why the count went up."""
        old = cite(EXPORT, K24, 13, first_page=20, text="carried over")
        kept = cite(EXPORT, K25, 13, first_page=20, text="carried over")
        fresh = [Mention(EXPORT.entity_id, K25.document_id, 5000 + i, f"{40 + i}_1", 40 + i,
                         text=f"new restriction {i}") for i in range(7)]
        t = build_timeline([K24, K25], TOPICS, old + kept + fresh)
        [e] = [e for e in t.events if e.topic]
        self.assertEqual((e.kind, e.paragraphs, e.previous_paragraphs), ("expanded", 20, 13))
        self.assertEqual(e.evidence.paragraph_id, "40_1")
        self.assertIs(e.evidence.new_paragraph, True)
        self.assertEqual(e.summary, "Cited in 20 paragraphs, up from 13 in the FY2024 10-K.")

    def test_a_lightly_edited_paragraph_is_carried_over_not_new(self):
        """Real FY2025 → FY2026 edit: one word dropped. Exact comparison calls
        this new; it is the same bullet."""
        was = ("•government actions or changes in governmental policies, such as export "
               "controls, increased restrictions on gaming usage, or tariffs; and")
        now = was.removesuffix(" and")
        old = [Mention(EXPORT.entity_id, K25.document_id, 1, "17_1", 17, text=was)]
        new = [Mention(EXPORT.entity_id, K26.document_id, 2, "16_1", 16, text=now)] + [
            Mention(EXPORT.entity_id, K26.document_id, 3 + i, f"{27 + i}_1", 27 + i,
                    text=f"In May 2025 the USG announced rule number {i} restricting "
                         f"exports of data center GPUs to China.") for i in range(3)]
        t = build_timeline([K25, K26], TOPICS, old + new)
        [e] = [e for e in t.events if e.topic]
        self.assertEqual((e.kind, e.evidence.paragraph_id, e.evidence.new_paragraph),
                         ("expanded", "27_1", True))

    def test_a_substantially_rewritten_paragraph_is_new(self):
        old = shingles("On January 15, 2025, the USG published the AI Diffusion IFR in the "
                       "Federal Register, imposing a worldwide licensing requirement.")
        self.assertLess(overlap("In May 2025, the USG announced that it would rescind the "
                                "AI Diffusion IFR and implement a replacement rule.", old),
                        CARRIED_OVER)
        self.assertEqual(overlap("China  exports are\nRESTRICTED", shingles("China exports are restricted.")),
                         1.0, "case and whitespace are not changes")

    def test_a_fragment_cut_by_a_page_break_is_carried_over(self):
        """Real text. FY2025 breaks this FY2024 paragraph across pages 25 and
        26; the page-25 half is a small part of the old paragraph, so a
        paragraph-to-paragraph score calls it new. None of it is."""
        fy2024 = ("Government actions, including trade protection and national and economic "
                  "security policies of U.S. and foreign government bodies, such as tariffs, "
                  "import or export regulations, including deemed export restrictions and "
                  "restrictions on the activities of U.S. persons, trade and economic "
                  "sanctions, decrees, quotas or other trade barriers and restrictions could "
                  "affect our ability to ship products, provide services to our customers and "
                  "employees, do business without an export license with entities on the U.S. "
                  "Department of Commerce’s U.S. Entity List or other USG restricted parties "
                  "lists (which is expected to change from time to time), and generally fulfill "
                  "our contractual obligations and have a material adverse effect on our "
                  "business.")
        fy2025_page_25 = ("Government actions, including trade protection and national and "
                          "economic security policies of U.S. and foreign government bodies, "
                          "such as tariffs, import or export regulations, including deemed "
                          "export restrictions and restrictions on")
        self.assertEqual(overlap(fy2025_page_25, shingles(fy2024)), 1.0)

    def test_a_paragraph_joined_from_two_old_ones_is_carried_over(self):
        """The reverse: last year's page break split it, this year it is whole."""
        a = "Export controls could disrupt our supply chain and distribution channels,"
        b = "negatively impacting our ability to serve demand in China and elsewhere."
        whole = f"{a} {b}"
        old = [Mention(EXPORT.entity_id, K24.document_id, 1, pid, pg, text=t)
               for pid, pg, t in (("20_9", 20, a), ("21_1", 21, b))]
        new = [Mention(EXPORT.entity_id, K25.document_id, 2, "30_1", 30, text=whole)] + [
            Mention(EXPORT.entity_id, K25.document_id, 3 + i, f"{31 + i}_1", 31 + i,
                    text=f"Entirely new rule {i} on accelerated computing exports.")
            for i in range(4)]                       # 2 → 5 paragraphs: expanded
        t = build_timeline([K24, K25], TOPICS, old + new)
        [e] = [e for e in t.events if e.topic]
        self.assertEqual((e.kind, e.evidence.paragraph_id), ("expanded", "31_1"))

    def test_when_every_paragraph_is_carried_over_the_first_is_used(self):
        old = cite(CHINA, K24, 3, text="same text")
        new = cite(CHINA, K25, 3, text="same text") + cite(CHINA, K25, 3, first_page=40,
                                                           text="same text")
        t = build_timeline([K24, K25], TOPICS, old + new)
        [e] = [e for e in t.events if e.topic]
        self.assertEqual((e.evidence.paragraph_id, e.evidence.new_paragraph), ("20_1", False))

    def test_without_text_the_first_mention_is_used_and_newness_is_unknown(self):
        t = build_timeline([K24, K25], TOPICS, cite(EXPORT, K24, 13) + cite(EXPORT, K25, 20))
        [e] = [e for e in t.events if e.topic]
        self.assertEqual(e.evidence.paragraph_id, "20_1")
        self.assertIsNone(e.evidence.new_paragraph)

    def test_a_dropped_topic_points_at_where_it_last_appeared(self):
        t = build_timeline([K25, K26], TOPICS, cite(DGX, K25, 1, first_page=15))
        [e] = [e for e in t.events if e.topic]
        self.assertEqual(e.kind, "no_longer_disclosed")
        self.assertEqual(e.evidence.document_id, K25.document_id)
        self.assertEqual(e.evidence.page, 15)

    def test_every_change_has_evidence(self):
        ms = (cite(EXPORT, K24, 13) + cite(EXPORT, K25, 20) + cite(DGX, K24, 3)
              + cite(H20, K25, 4) + cite(CHINA, K24, 10) + cite(CHINA, K25, 2))
        t = build_timeline([K24, K25], TOPICS, ms)
        changes = [e for e in t.events if e.kind != "filed"]
        self.assertEqual(len(changes), 4)
        for e in changes:
            with self.subTest(e.title):
                self.assertIsNotNone(e.evidence)


class History(unittest.TestCase):
    def test_a_topic_that_returns_is_disclosed_again_not_new(self):
        steady = cite(EXPORT, K24, 1) + cite(EXPORT, K25, 1) + cite(EXPORT, K26, 1)
        t = build_timeline([K24, K25, K26], TOPICS,
                           steady + cite(DGX, K24, 3) + cite(DGX, K26, 2))
        self.assertEqual(kinds(t, filing=K25), {"dgx_cloud": "no_longer_disclosed"})
        self.assertEqual(kinds(t, filing=K26), {"dgx_cloud": "disclosed_again"})

    def test_first_in_a_10k_after_a_10q_named_it_says_so(self):
        """Still new to the annual report, but not new to the company's filings."""
        q3 = Filing(8, "0000000001-24-000004", "10-Q", date(2024, 11, 20), "Q3 FY2025")
        t = build_timeline([K24, q3, K25], TOPICS,
                           cite(EXPORT, K24, 1) + cite(EXPORT, K25, 1)
                           + cite(H20, q3, 1) + cite(H20, K25, 2))
        [e] = [e for e in t.events if e.topic]
        self.assertEqual(e.kind, "newly_disclosed")
        self.assertEqual(e.named_earlier_in, ("10-Q",))
        self.assertEqual(e.summary,
                         "Cited in 2 paragraphs; first time in a 10-K, named earlier in a 10-Q.")


class Ordering(unittest.TestCase):
    def test_newest_filing_first_filed_first_then_biggest_change(self):
        ms = (cite(EXPORT, K24, 13) + cite(EXPORT, K25, 26)
              + cite(CHINA, K24, 13) + cite(CHINA, K25, 17)
              + cite(DGX, K24, 3) + cite(H20, K25, 4))
        t = build_timeline([K24, K25], TOPICS, ms)
        self.assertEqual([e.title for e in t.events], [
            "FY2025 10-K Filed", "H20 Newly Disclosed", "Export Controls Expanded",
            "China Expanded", "DGX Cloud No Longer Disclosed", "FY2024 10-K Filed"])

    def test_keys_are_unique_and_built_from_accession_and_slug(self):
        ms = cite(EXPORT, K24, 13) + cite(EXPORT, K25, 26) + cite(H20, K25, 1)
        t = build_timeline([K24, K25], TOPICS, ms)
        keys = [e.key for e in t.events]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(f"{K25.accession}:expanded:export_controls", keys)
        self.assertIn(f"{K25.accession}:filed:", keys)


if __name__ == "__main__":
    unittest.main()
