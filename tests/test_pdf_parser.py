"""PDF parsing: paragraphs and their bounding boxes, against known geometry.

Every PDF here is written by `pdfgen`, which records where it put each line,
so a box is checked against where the text is — not against what the parser
thinks. Tolerance is a hundredth of a point.

The first test is the bug this parser replaced: pypdf's plain text has no
blank line between paragraphs, so splitting on blank lines returned one
paragraph per page.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pdfgen import write_pdf

from evident_parser.anchors import paragraph_page
from evident_parser.chunker import chunk_document
from evident_parser.pdf import parse_pdf

PARAS = [
    "Export controls restrict shipments of our data center products to China. " * 3,
    "Supply chain concentration in Taiwan is a risk to our business. " * 2,
    "Short (one line).",
]
TOL = 0.01


class _Pdf(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self._dir.name) / "f.pdf")

    def tearDown(self):
        self._dir.cleanup()

    def parse(self, pages, **layout):
        placed = write_pdf(self.path, pages, **layout)
        sections, blocks, tables, n = parse_pdf(self.path, accession="0000000000-26-000001")
        return placed, sections, blocks, n

    def assertBoxesMatch(self, blocks, placed):
        self.assertEqual(len(blocks), len(placed))
        for b, want in zip(blocks, placed):
            with self.subTest(b.paragraph_id):
                self.assertEqual(b.text, want.text)
                self.assertIsNotNone(b.bbox)
                got = (b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1)
                for g, w in zip(got, want.box):
                    self.assertAlmostEqual(g, w, delta=TOL, msg=f"{got} != {want.box}")
                self.assertEqual((b.bbox.page_width, b.bbox.page_height), (612.0, 792.0))


class Paragraphs(_Pdf):
    def test_paragraphs_on_one_page_are_separate(self):
        """The old parser returned 1 here."""
        _, _, blocks, _ = self.parse([PARAS])
        self.assertEqual([b.text for b in blocks], [p.strip() for p in PARAS])

    def test_ids_are_positional_so_the_page_can_be_read_from_them(self):
        _, _, blocks, n = self.parse([PARAS, PARAS[:1]])
        self.assertEqual(n, 2)
        self.assertEqual([b.paragraph_id for b in blocks], ["1_1", "1_2", "1_3", "2_1"])
        self.assertEqual([paragraph_page(b.paragraph_id) for b in blocks], [1, 1, 1, 2])
        self.assertEqual([b.page_number for b in blocks], [1, 1, 1, 2])

    def test_a_first_line_indent_alone_separates_paragraphs(self):
        _, _, blocks, _ = self.parse([PARAS], para_gap=0, indent=24)
        self.assertEqual(len(blocks), 3)

    def test_each_bullet_is_a_paragraph(self):
        bullets = ["• First bullet about export controls that runs long enough to wrap "
                   "onto a second line of text.", "• Second bullet.", "• Third."]
        _, _, blocks, _ = self.parse([bullets], para_gap=0)
        self.assertEqual([b.text for b in blocks], bullets)

    def test_item_headings_are_sections_not_paragraphs(self):
        _, sections, blocks, _ = self.parse([["Item 1A. Risk Factors", PARAS[0]]])
        self.assertEqual([s.title for s in sections], ["Item 1A. Risk Factors"])
        self.assertEqual([(b.paragraph_id, b.section_ordinal) for b in blocks], [("1_1", 0)])


class Boxes(_Pdf):
    def test_boxes_are_where_the_text_is(self):
        placed, _, blocks, _ = self.parse([PARAS, PARAS[:1]])
        self.assertBoxesMatch(blocks, placed)
        self.assertEqual([b.bbox.page for b in blocks], [1, 1, 1, 2])

    def test_widths_come_from_the_fonts_own_metrics(self):
        """A non-standard font with irregular widths declared in the file:
        any built-in metrics would put the right edge in the wrong place."""
        placed, _, blocks, _ = self.parse([PARAS], font="custom")
        self.assertBoxesMatch(blocks, placed)

    def test_non_ascii_characters_are_measured_by_their_code(self):
        """`’` is byte 0x92 in the file; its width is filed under 0x92. Looked
        up as the decoded character it is missing, and a default is used."""
        typographic = ["NVIDIA’s “export” rules — • ‘quoted’ – dashes — and “more” ’’’’’’’’’’’’’’"]
        placed, _, blocks, _ = self.parse([typographic], font="custom")
        self.assertBoxesMatch(blocks, placed)

    def test_size_set_in_the_text_matrix(self):
        placed, _, blocks, _ = self.parse([PARAS], size_via="tm")
        self.assertBoxesMatch(blocks, placed)

    def test_size_set_by_the_page_transform(self):
        placed, _, blocks, _ = self.parse([PARAS], size_via="cm", font="custom")
        self.assertBoxesMatch(blocks, placed)

    def test_measured_from_the_pages_corner_not_the_origin(self):
        placed, _, blocks, _ = self.parse([PARAS], media_origin=(30, 40))
        self.assertBoxesMatch(blocks, placed)

    def test_a_first_line_indent_is_inside_the_box(self):
        placed, _, blocks, _ = self.parse([PARAS], para_gap=0, indent=24)
        self.assertBoxesMatch(blocks, placed)
        self.assertEqual(blocks[2].bbox.x0, 72 + 24, "the one-line paragraph starts indented")

    def test_boxes_are_top_left_origin_and_ordered_down_the_page(self):
        _, _, blocks, _ = self.parse([PARAS])
        tops = [b.bbox.y0 for b in blocks]
        self.assertEqual(tops, sorted(tops))
        for b in blocks:
            self.assertLess(b.bbox.y0, b.bbox.y1)
            self.assertLess(b.bbox.x0, b.bbox.x1)

    def test_a_rotated_page_gets_no_box_rather_than_a_wrong_one(self):
        _, _, blocks, _ = self.parse([PARAS], rotate=90)
        self.assertEqual(len(blocks), 3, "the text is still read")
        self.assertEqual([b.bbox for b in blocks], [None, None, None])


class Chunks(_Pdf):
    def test_chunks_carry_each_paragraphs_box(self):
        _, sections, blocks, _ = self.parse([PARAS, PARAS[:1]])
        chunks = chunk_document(accession="a", blocks=blocks, tables=[], sections=sections)
        boxes = {pid: box for c in chunks for pid, box in c.paragraph_boxes.items()}
        self.assertEqual(boxes, {b.paragraph_id: b.bbox for b in blocks})
        for c in chunks:
            self.assertLessEqual(set(c.paragraph_boxes), set(c.paragraph_ids))

    def test_sentence_split_parts_keep_their_paragraphs_box(self):
        long = "Export controls are complex and change often. " * 60
        _, sections, blocks, _ = self.parse([[long]])
        chunks = chunk_document(accession="a", blocks=blocks, tables=[], sections=sections,
                                max_tokens=200)
        parts = [pid for c in chunks for pid in c.paragraph_ids]
        self.assertTrue(all("#" in p for p in parts), parts)
        self.assertEqual({c.paragraph_boxes[p] for c in chunks for p in c.paragraph_ids},
                         {blocks[0].bbox})


if __name__ == "__main__":
    unittest.main()
