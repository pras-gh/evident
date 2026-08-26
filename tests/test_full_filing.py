"""Full-filing parsing and chunking.

The measurements these assert against were taken from the real NVDA FY2025 10-K
(nvda-20250126.htm) in a browser, because SEC blocks this network at its edge:

    2.08 MB · 87 pages · 27 sections · ~1,130 paragraphs · 68 tables
    zero <p> tags — all div/span
    18,740 chars of inline XBRL inside <div style="display:none"><ix:header>
    one 19,834-char "paragraph" that was entirely XBRL metadata

`tests/fixtures/.../nvda-20250126.htm` reproduces that profile at scale
(tools/make_large_fixture.py), so the Python implementation is exercised on a
document of realistic size and shape.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from evident_parser.chunker import (DEFAULT_MAX_TOKENS, chunk_document,
                                    split_oversized)
from evident_parser.html import parse_html
from evident_parser.models import Block, Table

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "edgar" / "Archives" /
           "edgar" / "data" / "1045810" / "000104581025000023" /
           "nvda-20250126.htm")
ACC = "0001045810-25-000023"


class InlineXbrl(unittest.TestCase):
    """A real filing hides ~19KB of XBRL behind display:none. Ingesting it
    produced one enormous junk chunk at the top of every document."""

    def test_display_none_subtree_is_skipped(self):
        html = ('<html><body>'
                '<div style="display:none"><ix:header>'
                'fasb.org us-gaap AccruedLiabilitiesCurrent P1Y P2Y false 491'
                '</ix:header></div>'
                '<div>Real prose that should survive.</div></body></html>')
        _, blocks, _, _ = parse_html(html, accession=ACC)
        text = " ".join(b.text for b in blocks)
        self.assertIn("Real prose", text)
        self.assertNotIn("fasb.org", text)
        self.assertNotIn("P1Y", text)

    def test_nested_content_inside_a_hidden_div_stays_skipped(self):
        html = ('<html><body><div style="display:none">'
                '<div><span>hidden</span><table><tr><td>hidden</td></tr></table></div>'
                '</div><div>visible</div></body></html>')
        _, blocks, tables, _ = parse_html(html, accession=ACC)
        self.assertEqual([b.text for b in blocks], ["visible"])
        self.assertEqual(tables, [], "a table inside a hidden subtree was captured")

    def test_visible_content_after_a_hidden_block_is_not_lost(self):
        """The skip must end at the right depth, or the rest of the filing
        disappears — which would be truncation for real."""
        html = ('<html><body><div style="display:none">x</div>'
                + "".join(f"<div>para {i}</div>" for i in range(50))
                + '</body></html>')
        _, blocks, _, _ = parse_html(html, accession=ACC)
        self.assertEqual(len(blocks), 50)


class Pagination(unittest.TestCase):
    def test_counts_every_page_break(self):
        html = ('<html><body>'
                + "".join(f'<div>page {i} body</div>'
                          f'<hr style="page-break-after:always">' for i in range(1, 87))
                + '<div>page 87 body</div></body></html>')
        _, blocks, _, pages = parse_html(html, accession=ACC)
        self.assertEqual(pages, 87)
        self.assertEqual(blocks[0].page_number, 1)
        self.assertEqual(blocks[-1].page_number, 87)

    def test_void_hr_does_not_unbalance_depth_tracking(self):
        """<hr> never closes; if it were counted as nesting, the hidden-subtree
        depth check would drift and start swallowing real content."""
        html = ('<html><body><div style="display:none">x</div>'
                '<hr style="page-break-after:always">'
                '<div>after</div></body></html>')
        _, blocks, _, pages = parse_html(html, accession=ACC)
        self.assertEqual([b.text for b in blocks], ["after"])
        self.assertEqual(pages, 2)


class Ceiling(unittest.TestCase):
    def test_oversized_paragraph_splits_and_keeps_its_identity(self):
        block = Block(paragraph_id="p_abc", ordinal=0,
                      text="This is a risk factor sentence. " * 200, page_number=28)
        parts = split_oversized(block, max_tokens=DEFAULT_MAX_TOKENS)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(p.paragraph_id.startswith("p_abc#") for p in parts))
        self.assertTrue(all(len(p.text) <= DEFAULT_MAX_TOKENS * 4 for p in parts))

    def test_a_sentence_longer_than_the_ceiling_is_still_split(self):
        """No punctuation to split on — a run-together table of contents. The
        ceiling has to hold anyway."""
        block = Block(paragraph_id="p_x", ordinal=0, text="word " * 3000)
        parts = split_oversized(block, max_tokens=DEFAULT_MAX_TOKENS)
        self.assertTrue(all(len(p.text) <= DEFAULT_MAX_TOKENS * 4 for p in parts))

    def test_short_paragraph_is_untouched(self):
        block = Block(paragraph_id="p_y", ordinal=0, text="Short.")
        self.assertEqual(split_oversized(block, max_tokens=600)[0].paragraph_id, "p_y")


class Tables(unittest.TestCase):
    def test_long_table_splits_with_the_header_on_every_part(self):
        """A body row without its header is a list of numbers with no meaning."""
        cells = [["(in millions)", "FY2025", "FY2024"]]
        cells += [[f"Segment {i}", f"{i*137:,}", f"{i*91:,}"] for i in range(300)]
        table = Table(table_id="t_1", ordinal=0, cells=cells, page_number=44)
        chunks = chunk_document(accession=ACC, blocks=[], tables=[table])
        self.assertGreater(len(chunks), 1, "a 300-row table stayed one chunk")
        for c in chunks:
            self.assertLessEqual(c.token_estimate, DEFAULT_MAX_TOKENS)
            self.assertTrue(c.text.splitlines()[0].startswith("(in millions)"))


class FullFilingScale(unittest.TestCase):
    """The whole point: a document of real size, parsed and chunked completely."""

    @classmethod
    def setUpClass(cls):
        html = FIXTURE.read_text()
        cls.html = html
        cls.sections, cls.blocks, cls.tables, cls.pages = parse_html(
            html, accession=ACC)
        cls.chunks = chunk_document(accession=ACC, blocks=cls.blocks,
                                    tables=cls.tables, sections=cls.sections)
        cls.split = [p for b in cls.blocks
                     for p in split_oversized(b, max_tokens=DEFAULT_MAX_TOKENS)]

    def test_the_document_is_actually_large(self):
        self.assertGreater(len(self.html), 500_000)

    def test_parses_every_page(self):
        self.assertEqual(self.pages, 87)

    def test_finds_the_filing_structure(self):
        titles = " ".join(s.title for s in self.sections)
        self.assertGreater(len(self.sections), 20)
        for expected in ("Item 1. Business", "Item 1A. Risk Factors",
                         "Item 7.", "Item 8."):
            self.assertIn(expected, titles)

    def test_produces_hundreds_of_chunks_not_a_handful(self):
        self.assertGreater(len(self.blocks), 800)
        self.assertGreater(len(self.chunks), 200)

    def test_no_chunk_exceeds_the_ceiling(self):
        over = [c for c in self.chunks if c.token_estimate > DEFAULT_MAX_TOKENS]
        self.assertEqual(over, [], f"{len(over)} chunks over the ceiling")

    def test_every_paragraph_reaches_a_chunk(self):
        covered = {p for c in self.chunks for p in c.paragraph_ids}
        missing = {b.paragraph_id for b in self.split} - covered
        self.assertEqual(missing, set(), f"{len(missing)} paragraphs unreachable")

    def test_no_chunk_spans_a_section(self):
        owner = {b.paragraph_id: b.section_ordinal for b in self.split}
        for c in self.chunks:
            if c.kind != "prose":
                continue
            self.assertEqual(len({owner.get(p) for p in c.paragraph_ids}), 1)

    def test_no_xbrl_metadata_leaked_into_the_body(self):
        self.assertEqual([b for b in self.blocks if "fasb.org" in b.text], [])

    def test_every_prose_chunk_carries_its_paragraph_ids(self):
        self.assertTrue(all(c.paragraph_ids for c in self.chunks
                            if c.kind == "prose"))

    def test_pages_are_recorded_across_the_whole_document(self):
        pages = {b.page_number for b in self.blocks}
        self.assertGreater(max(pages), 80)
        self.assertEqual(min(pages), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
