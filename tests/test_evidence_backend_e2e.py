"""The evidence viewer's backend, end to end in real Postgres.

    GET  /v1/company/{ticker}/documents
    GET  /v1/document/{id}/page/{page}
    GET  /v1/evidence/{chunk_id}             bounding boxes, highlights
    POST /v1/evidence/resolve                100+ citations

Two corpora, because they fail differently:

* **HTML** — the three real NVIDIA 10-Ks. No page geometry exists, so boxes
  must be null, and the anchor on the right page is the exact target.
* **PDF** — the same kind of real Risk Factors text, laid out as a PDF by
  `pdfgen`, which records where every line went. Boxes are checked against
  that, through ingest, chunking, storage and the API.

The invariant throughout: every highlight a citation resolves to is a block on
the page it names, as the page API serves it.

Skipped unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from pdfgen import paginate, write_pdf
from test_evidence_e2e import _ApiMixin, _reset_schema
from test_ingest_e2e import DSN, fixture_origin
from test_timeline_e2e import FY24, FY25, FY26, _ingest_and_extract

ROOT = Path(__file__).resolve().parents[1]
TIMELINE = ROOT / "tests" / "fixtures" / "edgar-timeline"
#: not a real filing — a PDF made for this test from real paragraphs
PDF_ACCESSION = "0001045810-99-000001"


async def _get(test, client, path, status=200, **params):
    r = await client.get(path, params=params or None)
    test.assertEqual(r.status_code, status, r.text)
    return r.json()


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class HtmlFilings(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        _ingest_and_extract(filings=3)

    async def test_documents_newest_first_with_what_each_supports(self):
        async with await self._client() as c:
            d = await _get(self, c, "/v1/company/nvda/documents")
        self.assertEqual((d["ticker"], d["company"]), ("NVDA", "NVIDIA CORP"))
        self.assertEqual([x["accession"] for x in d["documents"]], [FY26, FY25, FY24])
        fy26 = d["documents"][0]
        self.assertEqual((fy26["form_type"], fy26["fiscal_period"], fy26["filed_at"],
                          fy26["source_format"], fy26["page_count"]),
                         ("10-K", "FY2026", "2026-02-25", "html", 85))
        self.assertIs(fy26["has_bounding_boxes"], False)
        self.assertEqual(fy26["url"], "https://www.sec.gov/Archives/edgar/data/1045810/"
                                      "000104581026000021/0001045810-26-000021-index.htm")
        # Item 1A of FY2026 runs from page 13 to 30; paragraphs start on 13
        self.assertEqual((fy26["first_page"], fy26["paragraph_count"]), (13, 62))

    async def test_documents_filter_and_404(self):
        async with await self._client() as c:
            d = await _get(self, c, "/v1/company/NVDA/documents", form_type="10-q")
            self.assertEqual(d["documents"], [])
            d = await _get(self, c, "/v1/company/NVDA/documents", limit=1)
            self.assertEqual(len(d["documents"]), 1)
            await _get(self, c, "/v1/company/ZZZZ/documents", status=404)

    async def test_every_page_matches_the_full_reading_view(self):
        """The page API and the reading view build blocks the same way, so a
        client can use either and land on the same anchors."""
        async with await self._client() as c:
            for doc in (await _get(self, c, "/v1/company/NVDA/documents"))["documents"]:
                full = await _get(self, c, f"/v1/documents/{doc['document_id']}/pages")
                self.assertEqual(len(full["pages"]), doc["pages_with_text"])
                for p in full["pages"]:
                    with self.subTest(doc=doc["accession"], page=p["page"]):
                        one = await _get(self, c, f"/v1/document/{doc['document_id']}"
                                                  f"/page/{p['page']}")
                        self.assertEqual(one["blocks"], p["blocks"])

    async def test_every_citation_highlights_a_block_on_the_page_it_names(self):
        """All of them: every mention extraction stored, resolved in one call."""
        from sqlalchemy import select
        from evident_db import EntityMention, session_scope
        with session_scope(DSN) as db:
            refs = [{"chunk_id": m.chunk_id, "paragraph_id": m.paragraph_id}
                    for m in db.execute(select(EntityMention)).scalars()]
        self.assertGreater(len(refs), 200)

        pages: dict[tuple[int, int], set[str]] = {}
        async with await self._client() as c:
            r = await c.post("/v1/evidence/resolve", json={"citations": refs})
            self.assertEqual(r.status_code, 200, r.text)
            for res in r.json()["citations"]:
                self.assertTrue(res["resolved"], res["error"])
                ev = res["evidence"]
                self.assertEqual(ev["anchors"], [h["anchor"] for h in ev["highlights"]])
                for h in ev["highlights"]:
                    self.assertIsNone(h["bounding_box"], "HTML has no page geometry")
                    key = (ev["document_id"], h["page"])
                    if key not in pages:
                        page = await _get(self, c, f"/v1/document/{key[0]}/page/{key[1]}")
                        pages[key] = {b["anchor"] for b in page["blocks"]}
                    self.assertIn(h["anchor"], pages[key])
                self.assertIsNone(ev["bounding_box"])

    async def test_pages_without_text_are_empty_not_missing(self):
        """Page 5 of the FY2026 10-K exists; this corpus stores only Item 1A."""
        async with await self._client() as c:
            doc = (await _get(self, c, "/v1/company/NVDA/documents"))["documents"][0]
            p = await _get(self, c, f"/v1/document/{doc['document_id']}/page/5")
            self.assertEqual((p["blocks"], p["prev_page"], p["next_page"]), ([], None, 13))
            self.assertEqual((p["page_count"], p["page_width"]), (85, None))

    async def test_prev_and_next_walk_every_page_with_text(self):
        async with await self._client() as c:
            doc = (await _get(self, c, "/v1/company/NVDA/documents"))["documents"][0]
            walked, page = [], doc["first_page"]
            while page is not None:
                p = await _get(self, c, f"/v1/document/{doc['document_id']}/page/{page}")
                self.assertTrue(p["blocks"])
                walked.append(page)
                page = p["next_page"]
            self.assertEqual(len(walked), doc["pages_with_text"])
            self.assertEqual(walked, sorted(walked))

    async def test_pages_outside_the_filing_are_404(self):
        async with await self._client() as c:
            doc = (await _get(self, c, "/v1/company/NVDA/documents"))["documents"][0]
            for page in (0, 86):
                body = await _get(self, c, f"/v1/document/{doc['document_id']}/page/{page}",
                                  status=404)
                self.assertIn("1–85", body["detail"])
            await _get(self, c, "/v1/document/999999/page/1", status=404)


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Batching(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    """More than 100 citations in one request: an entity named on sixty pages,
    or an answer drawing on several filings."""

    @classmethod
    def setUpClass(cls):
        _ingest_and_extract(filings=3)
        from sqlalchemy import select
        from evident_db import EntityMention, session_scope
        with session_scope(DSN) as db:
            cls.refs = [{"chunk_id": m.chunk_id, "paragraph_id": m.paragraph_id}
                        for m in db.execute(select(EntityMention)).scalars()]

    async def test_a_thousand_citations_in_one_request(self):
        refs = (self.refs * 5)[:1000]                     # repeats included
        async with await self._client() as c:
            started = time.perf_counter()
            r = await c.post("/v1/evidence/resolve", json={"citations": refs})
            elapsed = time.perf_counter() - started
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()["citations"]
        self.assertEqual([x["index"] for x in out], list(range(1000)))
        self.assertTrue(all(x["resolved"] for x in out))
        self.assertLess(elapsed, 5.0, f"{elapsed:.2f}s for 1,000 citations")

    async def test_a_repeated_citation_resolves_the_same_every_time(self):
        ref = self.refs[0]
        async with await self._client() as c:
            single = (await c.get(f"/v1/evidence/{ref['chunk_id']}",
                                  params={"paragraph_id": ref["paragraph_id"]})).json()
            many = (await c.post("/v1/evidence/resolve",
                                 json={"citations": [ref] * 150})).json()["citations"]
        self.assertEqual({json.dumps(x["evidence"], sort_keys=True) for x in many},
                         {json.dumps(single, sort_keys=True)})

    async def test_bad_citations_in_a_large_batch_are_reported_in_place(self):
        refs = list(self.refs[:120])
        refs[7] = {"chunk_id": 10**9}
        refs[111] = {"chunk_id": refs[110]["chunk_id"], "paragraph_id": "999_9"}
        async with await self._client() as c:
            out = (await c.post("/v1/evidence/resolve",
                                json={"citations": refs})).json()["citations"]
        bad = [x["index"] for x in out if not x["resolved"]]
        self.assertEqual(bad, [7, 111])
        self.assertIn("no chunk", out[7]["error"])

    async def test_over_the_limit_is_refused_whole(self):
        async with await self._client() as c:
            r = await c.post("/v1/evidence/resolve",
                             json={"citations": (self.refs * 10)[:1001]})
        self.assertEqual(r.status_code, 422)


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class PdfFiling(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    """A PDF filing: boxes, through every layer, against known geometry."""

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import select
        from evident_db import Company, Document, session_scope
        from evident_parser.html import parse_html
        from seed_demo import KeywordClient
        from workers.ingest_worker import ingest_ticker
        from workers.memory_builder import build_for_document

        # Real Risk Factors paragraphs, from the FY2025 10-K fixture
        html = (TIMELINE / "Archives/edgar/data/1045810/000104581025000023/"
                "nvda-20250126.htm").read_text()
        _, blocks, _, _ = parse_html(html, accession=FY25)
        paras = [b.text for b in blocks[:24]]

        cls._dir = tempfile.TemporaryDirectory()
        root = Path(cls._dir.name)
        (root / "files").mkdir()
        shutil.copy(TIMELINE / "files" / "company_tickers.json", root / "files")
        (root / "submissions").mkdir()
        (root / "submissions" / "CIK0001045810.json").write_text(json.dumps({
            "cik": "1045810", "name": "NVIDIA CORP", "tickers": ["NVDA"],
            "filings": {"recent": {
                "accessionNumber": [PDF_ACCESSION], "form": ["10-K"],
                "filingDate": ["2025-02-26"], "reportDate": ["2025-01-26"],
                "acceptanceDateTime": ["2025-02-26T21:48:33.000Z"],
                "primaryDocument": ["risk-factors.pdf"]}}}))
        folder = root / "Archives/edgar/data/1045810" / PDF_ACCESSION.replace("-", "")
        folder.mkdir(parents=True)
        # in an irregular-width font the parser can only measure correctly by
        # reading the font's own /Widths; 64 columns of its widest glyph fit
        # inside the margins
        pages = paginate(paras, columns=64, first_page_reserved=1)
        pages[0].insert(0, "Item 1A. Risk Factors")
        placed = write_pdf(folder / "risk-factors.pdf", pages, font="custom", columns=64)
        cls.page_count = len(pages)
        cls.truth = {p.text: p for p in placed if p.text != "Item 1A. Risk Factors"}

        _reset_schema()
        with fixture_origin(root):
            ingest_ticker("NVDA", form_types=["10-K"], limit=1, url=DSN)
        with session_scope(DSN) as db:
            company = db.execute(select(Company)).scalar_one()
            document = db.execute(select(Document)).scalar_one()
            cls.document_id = document.id
            build_for_document(db, company_id=company.id, document=document,
                               client=KeywordClient())

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def assertBox(self, box, want, page):
        self.assertIsNotNone(box)
        self.assertEqual((box["page"], box["page_width"], box["page_height"]),
                         (page, 612.0, 792.0))
        got = (box["x0"], box["y0"], box["x1"], box["y1"])
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, delta=0.01, msg=f"{got} != {want}")

    async def test_the_document_says_it_has_boxes(self):
        async with await self._client() as c:
            [doc] = (await _get(self, c, "/v1/company/NVDA/documents"))["documents"]
        self.assertEqual((doc["source_format"], doc["has_bounding_boxes"]), ("pdf", True))
        self.assertEqual(doc["paragraph_count"], len(self.truth),
                         "every paragraph separate — not one per page")

    async def test_every_paragraph_on_every_page_is_boxed_where_it_is(self):
        async with await self._client() as c:
            seen = 0
            page = 1
            while page is not None:
                p = await _get(self, c, f"/v1/document/{self.document_id}/page/{page}")
                self.assertEqual((p["page_width"], p["page_height"]), (612.0, 792.0))
                for b in p["blocks"]:
                    want = self.truth[b["text"]]
                    self.assertEqual(want.page, page)
                    self.assertBox(b["bounding_box"], want.box, page)
                    seen += 1
                page = p["next_page"]
        self.assertEqual(seen, len(self.truth))

    async def test_evidence_carries_the_cited_paragraphs_box(self):
        from sqlalchemy import select
        from evident_db import EntityMention, session_scope
        with session_scope(DSN) as db:
            refs = [{"chunk_id": m.chunk_id, "paragraph_id": m.paragraph_id}
                    for m in db.execute(select(EntityMention)).scalars()]
        self.assertTrue(refs)
        async with await self._client() as c:
            out = (await c.post("/v1/evidence/resolve",
                                json={"citations": refs})).json()["citations"]
        for res in out:
            ev = res["evidence"]
            with self.subTest(ev["paragraph_id"]):
                want = self.truth[ev["text"]]
                self.assertEqual(ev["page"], want.page)
                self.assertBox(ev["bounding_box"], want.box, want.page)
                [h] = ev["highlights"]
                self.assertEqual(h["bounding_box"], ev["bounding_box"])

    async def test_a_chunk_wide_citation_highlights_each_paragraph_on_its_own_page(self):
        async with await self._client() as c:
            full = await _get(self, c, f"/v1/documents/{self.document_id}/pages")
            # a chunk whose paragraphs span a page break
            by_chunk: dict[int, set[int]] = {}
            for p in full["pages"]:
                for b in p["blocks"]:
                    by_chunk.setdefault(b["chunk_id"], set()).add(p["page"])
            spanning = next(cid for cid, pages in by_chunk.items() if len(pages) > 1)
            ev = await _get(self, c, f"/v1/evidence/{spanning}")
        self.assertGreater(len({h["page"] for h in ev["highlights"]}), 1)
        for h in ev["highlights"]:
            text = next(b["text"] for p in full["pages"] for b in p["blocks"]
                        if b["anchor"] == h["anchor"])
            self.assertBox(h["bounding_box"], self.truth[text].box, h["page"])


if __name__ == "__main__":
    unittest.main()
