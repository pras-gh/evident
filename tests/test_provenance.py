"""chunk_hash and end-to-end provenance.

Two properties this suite protects:

  * a chunk's identity is `company + accession + page + normalised text`, so
    re-rendering a filing does not change it but repeated running headers on
    different pages stay distinct rows;
  * every extracted object can name the chunk, page and paragraph it came from.
"""
from __future__ import annotations

import unittest

from evident_memory.entities import Evidence
from evident_parser.chunker import chunk_document
from evident_parser.html import parse_html
from evident_parser.models import chunk_hash, positional_paragraph_id

CO, ACC = "0001045810", "0001045810-25-000023"


class ChunkHash(unittest.TestCase):
    def test_is_stable_across_whitespace_and_case(self):
        """EDGAR re-renders filings; a reflowed paragraph is the same chunk."""
        a = chunk_hash(company_id=CO, document_accession=ACC, page_number=42,
                       text="Export  controls\napply.")
        b = chunk_hash(company_id=CO, document_accession=ACC, page_number=42,
                       text="export controls apply.")
        self.assertEqual(a, b)

    def test_page_number_keeps_repeated_headers_distinct(self):
        """Measured on the real NVDA 10-K: without the page in the key, 36 texts
        collapse and "table of contents" alone appears 82 times."""
        same = dict(company_id=CO, document_accession=ACC, text="Table of Contents")
        self.assertNotEqual(chunk_hash(page_number=2, **same),
                            chunk_hash(page_number=57, **same))

    def test_scoped_to_company_and_filing(self):
        base = dict(page_number=7, text="Revenue increased.")
        self.assertNotEqual(
            chunk_hash(company_id=CO, document_accession=ACC, **base),
            chunk_hash(company_id="0000320193", document_accession=ACC, **base))
        self.assertNotEqual(
            chunk_hash(company_id=CO, document_accession=ACC, **base),
            chunk_hash(company_id=CO, document_accession="0000-00-0", **base))

    def test_is_a_sha256_hex_digest(self):
        h = chunk_hash(company_id=CO, document_accession=ACC, page_number=1, text="x")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_missing_page_does_not_crash(self):
        self.assertEqual(
            len(chunk_hash(company_id=CO, document_accession=ACC,
                           page_number=None, text="x")), 64)


class PositionalParagraphIds(unittest.TestCase):
    def test_reads_as_page_underscore_index(self):
        self.assertEqual(positional_paragraph_id(42, 7), "42_7")

    def test_parser_numbers_paragraphs_within_each_page(self):
        html = ('<html><body><div>one</div><div>two</div>'
                '<hr style="page-break-after:always">'
                '<div>three</div></body></html>')
        _, blocks, _, _ = parse_html(html, accession=ACC)
        self.assertEqual([b.paragraph_id for b in blocks], ["1_1", "1_2", "2_1"])

    def test_ids_are_unique_across_a_whole_filing(self):
        html = "<html><body>" + "".join(
            f"<div>para {i}</div>" + ('<hr style="page-break-after:always">'
                                      if i % 12 == 0 else "")
            for i in range(400)) + "</body></html>"
        _, blocks, _, _ = parse_html(html, accession=ACC)
        ids = [b.paragraph_id for b in blocks]
        self.assertEqual(len(ids), len(set(ids)), "paragraph ids collided")


class EntityProvenance(unittest.TestCase):
    def test_evidence_exposes_the_requested_shape(self):
        ev = Evidence(document_id="7", paragraph_id="42_7", page_number=42,
                      quote="Export controls apply.", chunk_hash="ab" * 32,
                      confidence=0.96)
        self.assertEqual(
            set(ev.as_provenance()),
            {"chunk_hash", "document_id", "page", "paragraph_id", "confidence"})
        self.assertEqual(ev.as_provenance()["paragraph_id"], "42_7")
        self.assertEqual(ev.as_provenance()["confidence"], 0.96)

    def test_confidence_is_optional_so_absence_is_not_a_zero(self):
        """A missing score must not read as 'confidently wrong'."""
        ev = Evidence(document_id="7", paragraph_id="1_1", page_number=1, quote="x")
        self.assertIsNone(ev.as_provenance()["confidence"])


class ChunksCarryHashableIdentity(unittest.TestCase):
    def test_every_chunk_hashes_distinctly_on_a_real_scale_document(self):
        from pathlib import Path
        fixture = (Path(__file__).resolve().parent / "fixtures" / "edgar" /
                   "Archives" / "edgar" / "data" / "1045810" /
                   "000104581025000023" / "nvda-20250126.htm")
        sections, blocks, tables, _ = parse_html(fixture.read_text(), accession=ACC)
        chunks = chunk_document(accession=ACC, blocks=blocks, tables=tables,
                                sections=sections)
        hashes = [chunk_hash(company_id=CO, document_accession=ACC,
                             page_number=c.page_start, text=c.text) for c in chunks]
        self.assertGreater(len(chunks), 200)
        self.assertEqual(len(hashes), len(set(hashes)),
                         "two chunks hashed the same — one would be dropped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
