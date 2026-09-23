"""The endpoints behind the home page and the memory dashboard, against the
three real NVIDIA 10-Ks in real Postgres.

    GET /v1/companies
    GET /v1/companies/{ticker}
    GET /v1/companies/{ticker}/cards
    GET /v1/companies/{ticker}/cards/{kind}

Skipped unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import unittest

from test_evidence_e2e import _ApiMixin
from test_ingest_e2e import DSN
from test_timeline_e2e import FY24, FY25, FY26, _ingest_and_extract


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Companies(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        _ingest_and_extract(filings=3)

    async def _get(self, path, status=200):
        async with await self._client() as c:
            r = await c.get(path)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    async def test_the_home_page_lists_companies(self):
        self.assertEqual(await self._get("/v1/companies"), [{
            "ticker": "NVDA", "name": "NVIDIA CORP", "cik": "0001045810",
            "document_count": 3, "latest_filing": "2026-02-25"}])

    async def test_company_summary(self):
        s = await self._get("/v1/companies/nvda")
        self.assertEqual((s["ticker"], s["document_count"], s["earliest_filing"],
                          s["latest_filing"]), ("NVDA", 3, "2024-02-21", "2026-02-25"))
        self.assertEqual(s["counts"], {"risk": 6, "geography": 4, "product": 3})
        self.assertIsNotNone(s["built_at"])
        await self._get("/v1/companies/ZZZZ", status=404)

    async def test_every_card_has_a_history_or_says_why_not(self):
        cards = await self._get("/v1/companies/NVDA/cards")
        self.assertEqual([c["kind"] for c in cards],
                         ["risks", "revenue", "products", "guidance", "capex", "litigation"])
        for c in cards:
            with self.subTest(c["kind"]):
                if c["revision_count"]:
                    self.assertIsNone(c["unavailable"])
                    self.assertIsNotNone(c["current"])
                else:
                    self.assertTrue(c["unavailable"])
                    self.assertIsNone(c["current"])
                    self.assertIsNone(c["last_updated_at"])

    async def test_the_risks_card_is_the_history_of_what_was_disclosed(self):
        card = await self._get("/v1/companies/NVDA/cards/risks")
        self.assertEqual((card["revision_count"], card["material_count"],
                          card["last_updated_at"]), (3, 2, "2026-02-25"))
        self.assertEqual(
            [(r["source_note"], r["summary"]) for r in card["history"]],
            [(f"FY2024 10-K · {FY24}", "First on record: 5 risk factors — Supply Chain, "
                                       "Export Controls, Climate Regulation and 2 more."),
             (f"FY2025 10-K · {FY25}", "1 new risk factor: AI Diffusion Rule."),
             (f"FY2026 10-K · {FY26}", "Restated without change.")])
        self.assertEqual(card["history"][1]["delta"],
                         {"added": ["AI Diffusion Rule"], "removed": [], "changed": []})

    async def test_card_evidence_resolves_to_the_paragraph_it_names(self):
        card = await self._get("/v1/companies/NVDA/cards/risks")
        current = card["current"]
        self.assertEqual(len(current["evidence"]), len(current["facts"]))
        async with await self._client() as c:
            for ev in current["evidence"]:
                with self.subTest(ev["entity_slug"]):
                    self.assertEqual((ev["accession"], ev["section_path"]),
                                     (FY26, ["Item 1A. Risk Factors"]))
                    r = await c.get(f"/v1/evidence/{ev['chunk_id']}",
                                    params={"paragraph_id": ev["paragraph_id"]})
                    self.assertEqual(r.status_code, 200, r.text)
                    self.assertEqual(r.json()["page"], ev["page_number"])
                    self.assertIn(ev["quote"], r.json()["text"])

    async def test_materially_drops_the_no_change_revisions(self):
        card = await self._get("/v1/companies/NVDA/cards/risks?materially=true")
        self.assertEqual([r["revision"] for r in card["history"]], [1, 2])
        self.assertEqual(card["revision_count"], 3, "the count still covers every revision")

    async def test_promises_are_not_built_and_say_so(self):
        body = await self._get("/v1/companies/NVDA/promises", status=501)
        self.assertIn("not built", body["detail"])

    async def test_an_unknown_card_is_a_404_naming_the_real_ones(self):
        body = await self._get("/v1/companies/NVDA/cards/headcount", status=404)
        self.assertIn("risks", body["detail"])


if __name__ == "__main__":
    unittest.main()
