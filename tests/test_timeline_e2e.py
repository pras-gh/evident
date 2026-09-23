"""Timeline API, against three real NVIDIA 10-Ks in real Postgres.

    Ingest (FY2024, FY2025, FY2026) → Extract → GET /v1/company/NVDA/timeline

The corpus is `fixtures/edgar-timeline`: verbatim Risk Factors paragraphs on
their real pages. Extraction is the keyword matcher from `tools/seed_demo.py`,
not Claude — deterministic, and every entity it records is literally in the
paragraph it cites — driven through the same `build_for_document` the worker
runs. So the expected events below are what these filings actually say, not
what a fixture was written to produce.

Skipped unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

from test_evidence_e2e import _ApiMixin, _reset_schema
from test_ingest_e2e import DSN, fixture_origin

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "edgar-timeline"
sys.path.insert(0, str(ROOT / "tools"))

FY24, FY25, FY26 = "0001045810-24-000029", "0001045810-25-000023", "0001045810-26-000021"

#: Every event these three filings produce. FY2024 is the baseline, so it
#: contributes only its `filed` event.
EXPECTED = [
    ("Feb 2026", "Filing", "FY2026 10-K Filed"),
    ("Feb 2026", "Product", "H20 Newly Disclosed"),
    ("Feb 2026", "Risk", "Export Controls Expanded"),
    ("Feb 2026", "Geography", "China Expanded"),
    ("Feb 2026", "Risk", "AI Diffusion Rule Narrowed"),
    ("Feb 2026", "Product", "DGX Cloud No Longer Disclosed"),
    ("Feb 2025", "Filing", "FY2025 10-K Filed"),
    ("Feb 2025", "Risk", "AI Diffusion Rule Newly Disclosed"),
    ("Feb 2025", "Product", "Blackwell Newly Disclosed"),
    ("Feb 2025", "Risk", "Export Controls Expanded"),
    ("Feb 2025", "Risk", "Tariffs Expanded"),
    ("Feb 2025", "Geography", "China Expanded"),
    ("Feb 2024", "Filing", "FY2024 10-K Filed"),
]


def _ingest_and_extract(filings: int) -> None:
    from sqlalchemy import select
    from evident_db import Company, Document, session_scope
    from seed_demo import KeywordClient
    from workers.ingest_worker import ingest_ticker
    from workers.memory_builder import build_for_document

    _reset_schema()
    with fixture_origin(CORPUS):
        ingested = ingest_ticker("NVDA", form_types=["10-K"], limit=filings, url=DSN).filings
    for f in sorted(ingested, key=lambda f: f.filed_at):
        with session_scope(DSN) as db:
            company = db.execute(select(Company)).scalar_one()
            document = db.execute(select(Document).where(
                Document.accession == f.accession)).scalar_one()
            build_for_document(db, company_id=company.id, document=document,
                               client=KeywordClient())


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class OneFiling(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    """The trap: with one filing, `first_seen` makes everything look new."""

    @classmethod
    def setUpClass(cls):
        _ingest_and_extract(filings=1)       # the newest, FY2026

    async def test_a_single_filing_yields_no_false_new_events(self):
        async with await self._client() as c:
            d = (await c.get("/v1/company/NVDA/timeline")).json()
        self.assertEqual([e["title"] for e in d["events"]], ["FY2026 10-K Filed"])
        self.assertEqual([(f["accession"], f["coverage"]) for f in d["filings"]],
                         [(FY26, "baseline")])

    async def test_even_though_the_entities_are_there(self):
        """The baseline is not empty — it has 12 entities, every one of which a
        first_seen timeline would announce as new."""
        async with await self._client() as c:
            memory = (await c.get("/v1/companies/NVDA/memory")).json()
        self.assertGreaterEqual(sum(memory["counts"].values()), 10)


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class ThreeYears(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        _ingest_and_extract(filings=3)

    async def _timeline(self, **params):
        async with await self._client() as c:
            r = await c.get("/v1/company/NVDA/timeline", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    async def test_the_events_are_what_the_filings_say(self):
        d = await self._timeline()
        self.assertEqual([(e["date_label"], e["category"], e["title"]) for e in d["events"]],
                         EXPECTED)
        self.assertEqual(d["total"], len(EXPECTED))

    async def test_counts_are_paragraphs_and_are_reported(self):
        d = await self._timeline(kind="expanded")
        got = {(e["filing"]["fiscal_period"], e["topic"]["slug"]):
               (e["previous_paragraphs"], e["paragraphs"]) for e in d["events"]}
        self.assertEqual(got, {
            ("FY2026", "export_controls"): (20, 26), ("FY2026", "china"): (17, 22),
            ("FY2025", "export_controls"): (13, 20), ("FY2025", "china"): (13, 17),
            ("FY2025", "tariffs"): (2, 7),
        })

    async def test_each_filing_is_compared_with_the_previous_10k(self):
        d = await self._timeline()
        self.assertEqual([(f["accession"], f["coverage"]) for f in d["filings"]],
                         [(FY26, "compared"), (FY25, "compared"), (FY24, "baseline")])
        pairs = {(e["filing"]["accession"], e["compared_with"]["accession"])
                 for e in d["events"] if e["compared_with"]}
        self.assertEqual(pairs, {(FY26, FY25), (FY25, FY24)})

    async def test_every_change_cites_a_paragraph_that_resolves_where_it_says(self):
        """The evidence is a real citation: the resolver returns the same page,
        the paragraph is on that page of the reading view, and it names the
        thing the event is about."""
        from seed_demo import VOCABULARY
        pattern = {name: re.compile(rx, re.I) for rx, name, _ in VOCABULARY}

        d = await self._timeline()
        changes = [e for e in d["events"] if e["kind"] != "filed"]
        self.assertEqual(len(changes), 10)
        async with await self._client() as c:
            for e in changes:
                ev = e["evidence"]
                with self.subTest(e["id"]):
                    # a drop has nothing in the new filing to cite
                    where = e["compared_with"] if e["kind"] == "no_longer_disclosed" else e["filing"]
                    self.assertEqual(ev["document_id"], where["document_id"])

                    r = await c.get(f"/v1/evidence/{ev['chunk_id']}",
                                    params={"paragraph_id": ev["paragraph_id"],
                                            "entity": e["topic"]["slug"]})
                    self.assertEqual(r.status_code, 200, r.text)
                    resolved = r.json()
                    self.assertEqual(resolved["page"], ev["page"])
                    self.assertEqual(resolved["document_id"], ev["document_id"])
                    self.assertRegex(resolved["text"], pattern[e["topic"]["name"]])
                    self.assertIn(f"p. {ev['page']}", ev["citation"])

                    pages = (await c.get(f"/v1/documents/{ev['document_id']}/pages")).json()
                    on_page = next(p for p in pages["pages"] if p["page"] == ev["page"])
                    self.assertIn(f"p-{ev['paragraph_id']}",
                                  [b["anchor"] for b in on_page["blocks"]])

    async def test_expansion_evidence_is_new_material_not_a_carried_over_paragraph(self):
        """Pointing at the first export-controls paragraph would show nothing
        about why the count rose. In FY2026 that is a bullet carried over from
        FY2025 with one word dropped (16_1), and every paragraph before 27_2 is
        carried over the same way — lightly edited or, like 26_1, a fragment of
        an old paragraph that this year's page break cut differently. The
        evidence is the first paragraph that is mostly new."""
        d = await self._timeline(entity="export_controls", kind="expanded")
        got = {e["filing"]["accession"]: e["evidence"] for e in d["events"]}
        self.assertEqual({acc: (ev["paragraph_id"], ev["new_paragraph"]) for acc, ev in got.items()},
                         {FY26: ("27_2", True), FY25: ("27_1", True)})
        self.assertTrue(got[FY26]["quote"].startswith(
            "The export controls applicable to China are complex"))
        self.assertTrue(got[FY25]["quote"].startswith(
            "Over the past three years, we have been subject to a series of shifting"))

    async def test_filters(self):
        risk = await self._timeline(category="risk")
        self.assertEqual({e["category"] for e in risk["events"]}, {"Risk"})
        self.assertEqual(risk["total"], 5)
        # facet counts ignore the category filter, so every chip can show its count
        self.assertEqual(risk["categories"],
                         {"Filing": 3, "Risk": 5, "Product": 3, "Geography": 2})

        h20 = await self._timeline(entity="h20")
        self.assertEqual([e["title"] for e in h20["events"]], ["H20 Newly Disclosed"])

        page = await self._timeline(limit=2)
        self.assertEqual((len(page["events"]), page["total"]), (2, 13))

    async def test_since_filters_events_without_changing_the_comparison(self):
        """Filtering to FY2026 must not make FY2026 a baseline."""
        d = await self._timeline(since="2026-01-01")
        self.assertEqual([e["title"] for e in d["events"]], [t for _, _, t in EXPECTED[:6]])

    async def test_bad_requests(self):
        async with await self._client() as c:
            self.assertEqual((await c.get("/v1/company/ZZZZ/timeline")).status_code, 404)
            self.assertEqual((await c.get("/v1/company/NVDA/timeline",
                                          params={"kind": "bogus"})).status_code, 422)

    async def test_filings_link_to_edgar_not_to_where_they_were_ingested_from(self):
        d = await self._timeline()
        self.assertEqual(d["filings"][0]["url"],
                         "https://www.sec.gov/Archives/edgar/data/1045810/"
                         "000104581026000021/0001045810-26-000021-index.htm")
        self.assertEqual([f["page_count"] for f in d["filings"]], [85, 87, 85])


if __name__ == "__main__":
    unittest.main()
