"""Tests for the dependency-free core: ids, parsing, chunking, embedding.

Written with unittest so they run with nothing installed; pytest collects them
too. The network, PDF and Postgres paths are excluded on purpose — they are
thin adapters over third-party libraries, and the logic worth protecting is all
in here.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


from evident_parser.chunker import chunk_document
from evident_retrieval.embed import HashingEmbedder
from evident_parser.models import Block, Table, content_id, normalise
from evident_parser.html import parse_html
from evident_parser.models import fiscal_period
from evident_retrieval.store import _vector_literal

ACC = "0000320193-25-000073"

FILING = """
<html><head><style>p{margin:0}</style></head><body>
<div><b>PART II</b></div>
<div>Item 7. Management&rsquo;s Discussion and Analysis</div>
<p>Capital expenditures for fiscal 2025 totaled $14.6 billion, compared to
$10.9 billion in fiscal 2024.</p>
<table><tr><td>(in millions)</td><td>2025</td><td>2024</td></tr>
<tr><td>Data centers</td><td>6,204</td><td>3,118</td></tr></table>
<div style="page-break-before:always"></div>
<p>Item 7 of this report describes the Company&rsquo;s liquidity position in detail,
and this sentence is long enough that the heading heuristic must not fire.</p>
<table><tr><td>layout only</td></tr></table>
<div>Item 8. Financial Statements</div>
<p>Refer to Note 4.</p>
</body></html>
"""


class ParagraphIds(unittest.TestCase):
    def test_survives_cosmetic_reflow(self):
        """EDGAR re-renders filings; a new line break is not a new paragraph."""
        self.assertEqual(
            content_id("p", ACC, "Hello  world"),
            content_id("p", ACC, "Hello\nworld"),
        )

    def test_changes_when_content_changes(self):
        self.assertNotEqual(content_id("p", ACC, "revenue rose"),
                            content_id("p", ACC, "revenue fell"))

    def test_scoped_to_the_filing(self):
        self.assertNotEqual(content_id("p", ACC, "same"),
                            content_id("p", "0000000000-00-000000", "same"))

    def test_normalise_is_case_and_space_insensitive(self):
        self.assertEqual(normalise("  The   COMPANY "), "the company")


class HtmlParsing(unittest.TestCase):
    def setUp(self):
        self.sections, self.blocks, self.tables, self.pages = parse_html(
            FILING, accession=ACC
        )

    def test_builds_a_section_path(self):
        item7 = self.sections[1]
        self.assertEqual(item7.level, 2)
        self.assertEqual(item7.path[0], "PART II")
        self.assertIn("Item 7", item7.path[1])

    def test_page_counter_advances_on_break(self):
        self.assertEqual(self.pages, 2)
        self.assertEqual(self.blocks[0].page_number, 1)
        self.assertEqual(self.blocks[1].page_number, 2)

    def test_cross_reference_is_not_a_heading(self):
        """'Item 7 of this report describes...' is prose, not a new section."""
        self.assertTrue(
            any(b.text.startswith("Item 7 of this report") for b in self.blocks)
        )
        self.assertEqual(len(self.sections), 3)

    def test_data_table_is_kept_whole(self):
        self.assertEqual(len(self.tables), 1)
        t = self.tables[0]
        self.assertEqual(t.cells[0], ["(in millions)", "2025", "2024"])
        self.assertEqual((t.n_rows, t.n_cols), (2, 3))

    def test_layout_table_is_demoted_to_prose(self):
        """SEC wraps layout in <table>; a one-column table is not data."""
        self.assertTrue(any(b.text == "layout only" for b in self.blocks))

    def test_every_paragraph_has_a_page(self):
        self.assertTrue(all(b.page_number for b in self.blocks))


class Chunking(unittest.TestCase):
    def setUp(self):
        self.blocks = [
            Block(paragraph_id=content_id("p", ACC, f"para {i}", salt=str(i)),
                  ordinal=i, text=f"Paragraph {i} about capacity. " * 5,
                  page_number=1 + i // 3, section_ordinal=1 if i < 5 else 2)
            for i in range(9)
        ]
        self.tables = [Table(table_id="t_x", ordinal=0, page_number=2,
                             section_ordinal=1,
                             cells=[["(in millions)", "2025"], ["Data centers", "6,204"]])]
        self.chunks = chunk_document(accession=ACC, blocks=self.blocks,
                                     tables=self.tables, target_tokens=120)

    def test_no_chunk_spans_a_section(self):
        owner = {b.paragraph_id: b.section_ordinal for b in self.blocks}
        for c in self.chunks:
            if c.kind != "prose":
                continue
            self.assertEqual(len({owner[p] for p in c.paragraph_ids}), 1,
                             f"chunk {c.ordinal} straddles a section boundary")

    def test_every_paragraph_is_retrievable(self):
        covered = {p for c in self.chunks for p in c.paragraph_ids}
        self.assertEqual(covered, {b.paragraph_id for b in self.blocks})

    def test_prose_chunks_keep_their_paragraph_ids(self):
        for c in self.chunks:
            if c.kind == "prose":
                self.assertTrue(c.paragraph_ids)

    def test_table_is_its_own_chunk(self):
        tables = [c for c in self.chunks if c.kind == "table"]
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].table_id, "t_x")
        self.assertEqual(tables[0].paragraph_ids, [])

    def test_page_range_is_recorded(self):
        for c in self.chunks:
            self.assertIsNotNone(c.page_start)
            self.assertLessEqual(c.page_start, c.page_end)

    def test_ordinals_are_contiguous(self):
        self.assertEqual([c.ordinal for c in self.chunks],
                         list(range(len(self.chunks))))


class Embedding(unittest.TestCase):
    def setUp(self):
        self.e = HashingEmbedder(dim=256)

    def test_is_deterministic(self):
        a = self.e.embed(["capital expenditure"])[0]
        b = self.e.embed(["capital expenditure"])[0]
        self.assertEqual(a, b)

    def test_reports_its_provenance(self):
        # provenance moved onto the provider: `embed` returns plain vectors,
        # but the row still records what produced it
        self.assertEqual((self.e.name, self.e.model, self.e.dim),
                         ("local", "hashing-v1", 256))
        self.assertEqual(len(self.e.embed(["x"])[0]), 256)

    def test_vectors_are_unit_length(self):
        v = self.e.embed(["data centre capacity expansion"])[0]
        self.assertAlmostEqual(sum(x * x for x in v) ** 0.5, 1.0, places=6)

    def test_one_vector_per_input(self):
        self.assertEqual(len(self.e.embed(["a", "b", "c"])), 3)


class Misc(unittest.TestCase):
    def test_fiscal_period(self):
        self.assertEqual(fiscal_period("10-K", "2025-09-27"), "FY2025")
        self.assertEqual(fiscal_period("10-Q", "2026-03-28"), "Q1 2026")
        self.assertIsNone(fiscal_period("8-K", None))

    def test_vector_literal_is_pgvector_text_form(self):
        self.assertEqual(_vector_literal([0.5, -0.25]), "[0.5,-0.25]")


if __name__ == "__main__":
    unittest.main(verbosity=2)
