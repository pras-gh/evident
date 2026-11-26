"""Search fetches a company's filings from EDGAR the first time it's asked about.

Searching "google" used to answer "No filings for Alphabet yet" for good. Now a
search naming a company with no filings here starts a fetch of its latest
annual report; the page polls the fetch, and the next search finds it.

EDGAR is the fixture tree (`fixtures/edgar-real`: NVIDIA's FY2025 10-K
excerpt, and an EDGAR company list with Apple in it but no Apple filings, so
an Apple fetch fails the way a real one can). Database tests skip unless
TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import parallx_parser.edgar as edgar
from parallx_parser.edgar import SecCompany

from test_evidence_e2e import _ApiMixin, _reset_schema
from test_ingest_e2e import DSN, fixture_origin

ROOT = Path(__file__).resolve().parents[1]
EDGAR = ROOT / "tests" / "fixtures" / "edgar-real"
NVDA_10K = "0001045810-25-000023"


class Client(unittest.TestCase):
    def tearDown(self):
        edgar._TICKER_MAP = None

    def test_a_gzipped_response_is_decompressed(self):
        """The client asked sec.gov for gzip and never decompressed it: the
        company list would not parse, and a filing would be garbage."""
        body = json.dumps({"0": {"cik_str": 1, "ticker": "X", "title": "X"}}).encode()

        class Resp:
            headers = {"Content-Encoding": "gzip"}

            def read(self):
                return gzip.compress(body)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.dict(os.environ, {"SEC_USER_AGENT": "Parallx test"}), \
                mock.patch("urllib.request.urlopen", lambda req, timeout: Resp()):
            self.assertEqual(edgar._get("https://www.sec.gov/files/company_tickers.json"), body)

    def test_a_company_is_found_by_ticker_or_by_name(self):
        edgar._TICKER_MAP = [SecCompany("0001652044", "GOOGL", "Alphabet Inc."),
                             SecCompany("0001652044", "GOOG", "Alphabet Inc."),
                             SecCompany("0000929008", "WCC", "WESCO INTERNATIONAL INC")]
        self.assertEqual(edgar.lookup(ticker="$goog").cik, "0001652044")
        self.assertEqual(edgar.lookup(name="Wesco International").ticker, "WCC")
        self.assertEqual(edgar.lookup(ticker="NOPE", name="Alphabet").ticker, "GOOGL")
        self.assertIsNone(edgar.lookup(name="Acme Private Holdings"))

    def test_without_a_contact_edgar_is_not_asked(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(edgar.has_user_agent())


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class OnDemand(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("EMBEDDING_PROVIDER", "hashing")

    def setUp(self):
        _reset_schema()
        self.launched: list[int] = []
        patch = mock.patch("api.routers.fetches.launch", self.launched.append)
        patch.start()
        self.addCleanup(patch.stop)

    def understands(self, *intents):
        """The model that reads queries, answering `intents` in order."""
        from parallx_ai.gateway import (Gateway, MemoryStore, internal_organization_id,
                                        set_gateway)
        from parallx_ai.gateway.config import from_dict
        from test_ai_gateway import RAW, Script, ok
        set_gateway(None)
        org = internal_organization_id(DSN)
        g = Gateway(from_dict(RAW, today=date(2026, 11, 22)),
                    {"groq": Script("groq", *[ok(text=json.dumps(i)) for i in intents]),
                     "gemini": Script("gemini"), "anthropic": Script("anthropic")},
                    MemoryStore(orgs={org: {}}), sleep=lambda s: None)
        set_gateway(g)
        self.addCleanup(set_gateway, None)

    async def search(self, query):
        async with await self._client() as c:
            r = await c.post("/v1/search", json={"query": query})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    async def fetch(self, fetch_id):
        async with await self._client() as c:
            return (await c.get(f"/v1/fetches/{fetch_id}")).json()

    def run_worker(self, fetch_id):
        from workers import fetch_worker
        return fetch_worker.run(fetch_id, url=DSN, render=False)

    NVIDIA = {"companies": [{"name": "NVIDIA", "ticker": "NVDA"}], "topic": ""}

    async def test_the_first_search_fetches_and_the_next_finds_it(self):
        with fixture_origin(EDGAR):
            self.understands(self.NVIDIA, self.NVIDIA, self.NVIDIA)
            first = await self.search("nvidia")
            [c] = first["companies"]
            self.assertEqual((c["covered"], c["fetch"], c["ticker"]), (False, "queued", "NVDA"))
            self.assertEqual(first["hits"], [])
            self.assertEqual(self.launched, [c["fetch_id"]])

            # a second search while it runs joins it rather than starting another
            again = await self.search("nvidia")
            self.assertEqual(again["companies"][0]["fetch_id"], c["fetch_id"])
            self.assertEqual(self.launched, [c["fetch_id"]])

            self.assertEqual(self.run_worker(c["fetch_id"]), "done")
            done = await self.fetch(c["fetch_id"])
            self.assertEqual((done["status"], done["accession"], done["form_type"]),
                             ("done", NVDA_10K, "10-K"))
            self.assertGreater(done["chunks"], 0)

            found = await self.search("nvidia")
        self.assertEqual(found["companies"],
                         [{"name": "NVIDIA CORP", "ticker": "NVDA", "covered": True,
                           "fetch": None, "fetch_id": None, "fetch_error": None,
                           "access": "existing"}])
        self.assertTrue(found["hits"], "fetched, embedded, and searchable")
        self.assertTrue(all(h["accession"] == NVDA_10K for h in found["hits"]))

    async def test_a_failed_fetch_is_reported_and_not_retried_at_once(self):
        apple = {"companies": [{"name": "Apple", "ticker": "AAPL"}], "topic": ""}
        with fixture_origin(EDGAR):
            self.understands(apple, apple)
            first = await self.search("apple")
            fid = first["companies"][0]["fetch_id"]
            self.assertEqual(self.run_worker(fid), "failed")
            again = await self.search("apple")
        [c] = again["companies"]
        self.assertEqual((c["fetch"], c["fetch_id"]), ("failed", fid))
        self.assertTrue(c["fetch_error"])
        self.assertEqual(self.launched, [fid], "no new fetch within RETRY_AFTER")

    async def test_a_company_the_sec_has_never_heard_of_is_said_so(self):
        with fixture_origin(EDGAR):
            self.understands({"companies": [{"name": "Acme Private Holdings", "ticker": None}],
                              "topic": ""})
            body = await self.search("acme private holdings")
        self.assertEqual(body["companies"][0]["fetch"], "not_on_edgar")
        self.assertEqual(self.launched, [])

    async def test_without_a_contact_for_the_sec_nothing_is_fetched(self):
        self.understands(self.NVIDIA)
        with mock.patch.dict(os.environ):
            os.environ.pop("SEC_USER_AGENT", None)
            body = await self.search("nvidia")
        self.assertEqual(body["companies"][0]["fetch"], "not_configured")
        self.assertEqual(self.launched, [])

    async def test_a_company_on_record_under_another_ticker_is_not_fetched_again(self):
        """GOOGL and GOOG are one CIK. Here: NVIDIA on record as NVDA, and a
        second share class, NVDB, in EDGAR's list."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "edgar"
            shutil.copytree(EDGAR, tree)
            listing = tree / "files" / "company_tickers.json"
            rows = json.loads(listing.read_text())
            rows["9"] = {"cik_str": 1045810, "ticker": "NVDB", "title": "NVIDIA CORP"}
            listing.write_text(json.dumps(rows))
            with fixture_origin(tree):
                self.understands(self.NVIDIA,
                                 {"companies": [{"name": "Team Green", "ticker": "NVDB"}],
                                  "topic": ""})
                fid = (await self.search("nvidia"))["companies"][0]["fetch_id"]
                self.run_worker(fid)
                body = await self.search("team green")
        self.assertEqual(body["companies"][0]["covered"], True)
        self.assertEqual(body["scope"], {"tickers": ["NVDA"], "reason": "named"})
        self.assertEqual(self.launched, [fid])

    async def test_an_unknown_fetch_is_a_404(self):
        async with await self._client() as c:
            r = await c.get("/v1/fetches/999")
        self.assertEqual(r.status_code, 404)


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Worker(unittest.TestCase):
    def setUp(self):
        _reset_schema()
        os.environ.setdefault("EMBEDDING_PROVIDER", "hashing")

    def queue(self, cik, ticker, name, status="queued"):
        from parallx_db import CompanyFetch, session_scope
        with session_scope(DSN) as db:
            row = CompanyFetch(cik=cik, ticker=ticker, name=name, status=status)
            db.add(row)
            db.flush()
            return row.id

    def test_a_fetch_that_is_no_longer_queued_is_left_alone(self):
        from workers import fetch_worker
        fid = self.queue("0001045810", "NVDA", "NVIDIA CORP", status="done")
        self.assertEqual(fetch_worker.run(fid, url=DSN, render=False), "done")

    def test_a_filing_with_no_readable_text_fails_and_is_read_once_it_can_be(self):
        """Real 10-Ks once parsed to nothing and the fetch said "done"; and
        a fetch after the parser was fixed skipped them as unchanged."""
        from parallx_db import Chunk, CompanyFetch, session_scope
        from workers import fetch_worker
        with fixture_origin(EDGAR):
            fid = self.queue("0001045810", "NVDA", "NVIDIA CORP")
            with mock.patch("workers.ingest_worker._parse", lambda raw, pdf, acc: ([], [], [], 1)):
                self.assertEqual(fetch_worker.run(fid, url=DSN, render=False), "failed")
            with session_scope(DSN) as db:
                self.assertIn("no text could be read", db.get(CompanyFetch, fid).error)
            again = self.queue("0001045810", "NVDA", "NVIDIA CORP")
            self.assertEqual(fetch_worker.run(again, url=DSN, render=False), "done")
        with session_scope(DSN) as db:
            self.assertGreater(db.get(CompanyFetch, again).chunks, 0)
            self.assertGreater(db.query(Chunk).count(), 0, "same bytes, read this time")

    def test_a_failure_records_why(self):
        from parallx_db import CompanyFetch, session_scope
        from workers import fetch_worker
        with fixture_origin(EDGAR):
            fid = self.queue("0000320193", "AAPL", "Apple Inc.")
            self.assertEqual(fetch_worker.run(fid, url=DSN, render=False), "failed")
        with session_scope(DSN) as db:
            row = db.get(CompanyFetch, fid)
            self.assertEqual(row.status, "failed")
            self.assertIn("404", row.error)
            self.assertIsNotNone(row.finished_at)


if __name__ == "__main__":
    unittest.main()
