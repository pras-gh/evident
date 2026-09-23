"""The render worker: filings laid out in Chrome, photographed, and every
paragraph's box measured on the same pixels.

What each test guards:

* **page parity** — the renderer counts pages by the parser's rule exactly; if
  they disagreed by one, every page number after the difference would be wrong
* **matching** — a paragraph is found whatever inline markup or hidden copies
  of its text surround it
* **ink** — the box drawn over the page image covers the paragraph's text:
  dark pixels inside, blank paper just outside
* **PDF** — page N of the PDF is page N of the filing

Needs Chrome (or `playwright install chromium`); skipped without it. The
same checks through ingest, the database and the API are in
tests/test_evidence_backend_e2e.py (RenderedFilings).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

try:
    # on its own thread: a Playwright loop on the main thread would break
    # every async test that runs in the same process
    from workers.render_worker import BrowserThread
    BROWSER = BrowserThread()
except Exception:  # no Playwright, no Chrome, no Chromium
    BROWSER = None

from evident_db import Chunk, Document
from evident_parser.chunker import chunk_document
from evident_parser.html import parse_html

BREAK = '<div style="page-break-after:always"></div>'

PARAS = {
    1: ["Export controls restrict shipments of our data center products to China.",
        "We depend on third parties to manufacture, assemble and test our products."],
    2: ["Supply chain concentration in Taiwan is a risk to our operations.",
        "Tariffs could raise the cost of the systems we sell."],
    3: ["Cyber-attacks could harm our business and reputation."],
}


def filing(pages: dict[int, list[str]], *, extra: str = "") -> str:
    body, page = [], 1
    for n in sorted(pages):
        while page < n:
            body.append(BREAK)
            page += 1
        body += [f"<p>{t}</p>" for t in pages[n]]
    return f"<html><body>{extra}{''.join(body)}</body></html>"


def rows(html: str, accession: str) -> tuple[Document, list[Chunk], int]:
    """Parse and chunk as ingest does, into unsaved rows the worker can read."""
    from evident_db import store
    sections, blocks, tables, pages = parse_html(html, accession=accession)
    chunks = chunk_document(accession=accession, blocks=blocks, tables=tables,
                            sections=sections, target_tokens=40)
    doc = Document(id=1, accession=accession, source_format="html", page_count=pages,
                   source_path=store.write(store.source_path(accession, "f.htm"),
                                           html.encode()))
    return doc, [Chunk(id=i + 1, text=c.text, paragraph_ids=c.paragraph_ids,
                       page_number=c.page_start, ordinal=c.ordinal)
                 for i, c in enumerate(chunks)], pages


@unittest.skipUnless(BROWSER, "needs Chrome or `playwright install chromium`")
class Rendering(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._old = os.environ.get("FILING_STORE")
        os.environ["FILING_STORE"] = self._dir.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("FILING_STORE", None)
        else:
            os.environ["FILING_STORE"] = self._old
        self._dir.cleanup()

    def render(self, html: str, accession: str = "0000000001-26-000001"):
        from workers.render_worker import render_html
        doc, chunks, pages = rows(html, accession)
        result, made = BROWSER.run(render_html, doc, chunks)
        return doc, chunks, pages, result, made

    def image(self, relative: str):
        from PIL import Image
        from evident_db import store
        return Image.open(store.resolve(relative)).convert("L")

    # ------------------------------------------------------------ page parity
    def test_pages_are_counted_exactly_as_the_parser_counts_them(self):
        """Breaks by style, by class text, and one inside a display:none
        subtree — which the parser ignores, so the renderer must too."""
        html = filing(PARAS).replace(
            "<body>", '<body><div style="display:none"><div style="page-break-before:always">'
                      'x</div></div>')
        html = html.replace(BREAK, '<hr class="page-break-after: always">', 1)
        _, _, pages, result, made = self.render(html)
        self.assertEqual(pages, 3)
        self.assertEqual(result.pages, pages)
        self.assertEqual([b["page"] for b in made["bands"]], [1, 2, 3])

    def test_every_paragraph_is_boxed_on_the_page_its_id_names(self):
        _, chunks, _, result, made = self.render(filing(PARAS))
        self.assertEqual((result.missing, result.misplaced), ([], []))
        self.assertEqual(result.boxed, sum(len(v) for v in PARAS.values()))
        for pid, box in made["boxes"].items():
            self.assertEqual(box["page"], int(pid.split("_")[0]), pid)

    # --------------------------------------------------------------- matching
    def test_inline_markup_and_entities_do_not_break_the_match(self):
        html = filing({1: ["Export<b>controls</b> &amp; <i>China</i>&nbsp;risk "
                           "remain&#8217;s material."]})
        _, _, _, result, _ = self.render(html)
        self.assertEqual((result.boxed, result.missing), (1, []))

    def test_a_hidden_copy_of_the_text_is_not_what_gets_boxed(self):
        """Inline XBRL hides machine copies of text; the box must be on the
        visible paragraph, not on an invisible one earlier in the file."""
        text = PARAS[1][0]
        html = filing(PARAS, extra=f'<div style="display:none"><p>{text}</p></div>')
        _, _, _, result, made = self.render(html)
        self.assertEqual(result.missing, [])
        box = made["boxes"]["1_1"]
        self.assertGreater(box["y1"] - box["y0"], 5, "an invisible match has no height")

    # -------------------------------------------------------------------- ink
    def test_each_box_covers_its_paragraphs_ink_and_nothing_else(self):
        from workers.render_worker import SCALE
        _, _, _, _, made = self.render(filing(PARAS))
        for pid, box in made["boxes"].items():
            with self.subTest(pid):
                img = self.image(made["images"][box["page"]][0])
                x0, y0, x1, y1 = (round(v * SCALE) for v in
                                  (box["x0"], box["y0"], box["x1"], box["y1"]))
                inside = img.crop((x0, y0, x1, y1))
                self.assertLess(inside.getextrema()[0], 100, "no text under the box")
                # a strip just above and just below: paper between paragraphs
                for strip in ((x0, y0 - 5, x1, y0 - 1), (x0, y1 + 1, x1, y1 + 5)):
                    self.assertGreater(img.crop(strip).getextrema()[0], 200,
                                       f"text outside the box at {strip}")

    def test_pages_without_text_are_not_photographed(self):
        _, _, _, result, made = self.render(filing({1: PARAS[1], 4: PARAS[3]}))
        self.assertEqual((result.pages, sorted(made["images"])), (4, [1, 4]))

    def test_page_images_are_letter_width_at_twice_the_pixels(self):
        from workers.render_worker import PAGE_HEIGHT_PX, PAGE_WIDTH_PX, SCALE
        _, _, _, _, made = self.render(filing(PARAS))
        full, thumb = made["images"][1]
        self.assertEqual(self.image(full).width, PAGE_WIDTH_PX * SCALE)
        self.assertGreaterEqual(self.image(full).height, PAGE_HEIGHT_PX * SCALE)
        self.assertEqual(self.image(thumb).width, 180)

    # -------------------------------------------------------------------- PDF
    def test_pdf_page_n_is_filing_page_n(self):
        from pypdf import PdfReader
        from evident_db import store
        _, _, pages, result, made = self.render(filing({1: PARAS[1], 3: PARAS[3]}))
        reader = PdfReader(store.resolve(made["pdf"]))
        self.assertEqual((result.pdf_pages, len(reader.pages)), (pages, 3))
        text = [" ".join((p.extract_text() or "").split()) for p in reader.pages]
        self.assertIn("Export controls restrict", text[0])
        self.assertEqual(text[1], "", "page 2 has no text in this filing")
        self.assertIn("Cyber-attacks", text[2])


    def test_a_paragraph_carrying_the_break_is_on_the_same_page_everywhere(self):
        """The parser starts the next page at the start tag of any element that
        carries a page break, `after` included, so this paragraph is page 2.
        Chrome's own print would put it at the bottom of page 1; the PDF must
        agree with the parser, the images and the boxes instead."""
        from pypdf import PdfReader
        from evident_db import store
        html = ("<html><body><p>Page one text.</p>"
                '<p style="page-break-after:always">Carried the break to page two.</p>'
                "<p>Also page two.</p></body></html>")
        _, _, pages, result, made = self.render(html)
        self.assertEqual(pages, 2)
        self.assertEqual(made["boxes"]["2_1"]["page"], 2)
        text = [" ".join((p.extract_text() or "").split())
                for p in PdfReader(store.resolve(made["pdf"])).pages]
        self.assertEqual(len(text), 2)
        self.assertIn("Carried the break to page two", text[1])
        self.assertNotIn("Carried the break", text[0])


class Store(unittest.TestCase):
    def test_paths_cannot_leave_the_store(self):
        from evident_db import store
        with tempfile.TemporaryDirectory() as d:
            os.environ["FILING_STORE"] = d
            try:
                for bad in ("../../.env", "/etc/passwd", "a/../../b"):
                    with self.subTest(bad), self.assertRaises(ValueError):
                        store.resolve(bad)
                self.assertEqual(store.resolve("x/y.webp"), Path(d).resolve() / "x" / "y.webp")
                self.assertEqual(store.page_path("../0001-26-1", 3), "0001-26-1/pages/3.webp")
            finally:
                os.environ.pop("FILING_STORE", None)


if __name__ == "__main__":
    unittest.main()
