"""The S&P 500, fetched once and kept current (`workers/sync_worker.py`).

EDGAR is a fixture tree served locally: NVIDIA's filings from
`fixtures/edgar-real`, plus a latest-filings feed listing a new NVIDIA 10-Q,
a 10-Q from a company nobody tracks, an amendment (not watched), and an
Apple 10-K that EDGAR cannot serve — the way a filing can be on the feed a
minute before it is in the company's filing list. The tracked universe is
NVIDIA and Apple. Database tests skip unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from parallx_parser import edgar
from parallx_parser.universe import load

from test_ingest_e2e import DSN, fixture_origin

ROOT = Path(__file__).resolve().parents[1]
EDGAR = ROOT / "tests" / "fixtures" / "edgar-real"
NVDA_10K, NVDA_10Q = "0001045810-25-000023", "0001045810-24-000316"
AAPL_10K = "0000320193-26-000001"

UNIVERSE = """# test universe
ticker,cik,name,sector,sub_industry,date_added
AAPL,0000320193,Apple Inc.,Information Technology,Hardware,1982-11-30
NVDA,0001045810,Nvidia,Information Technology,Semiconductors,2001-11-30
"""


def feed(*entries: tuple[str, str, str, str, datetime]) -> str:
    """An Atom feed like EDGAR's: (form, company, cik, accession, accepted)."""
    body = "".join(f"""
<entry>
<title>{form} - {name} ({cik}) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{acc}-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; {at.date()} &lt;b&gt;AccNo:&lt;/b&gt; {acc} &lt;b&gt;Size:&lt;/b&gt; 5 MB</summary>
<updated>{at.isoformat()}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="{form}"/>
<id>urn:tag:sec.gov,2008:accession-number={acc}</id>
</entry>""" for form, name, cik, acc, at in entries)
    return f"""<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<id>https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent</id>
<updated>2026-11-26T09:00:00-05:00</updated>{body}
</feed>"""


NOW = datetime.now(timezone.utc).replace(microsecond=0)
FEED = feed(("10-Q", "NVIDIA CORP", "0001045810", NVDA_10Q, NOW - timedelta(minutes=4)),
            ("10-Q", "Acme Unlisted Inc", "0000999999", "0000999999-26-000001",
             NOW - timedelta(minutes=6)),
            ("10-K/A", "NVIDIA CORP", "0001045810", "0001045810-26-000099",
             NOW - timedelta(minutes=8)),
            ("10-K", "Apple Inc.", "0000320193", AAPL_10K, NOW - timedelta(minutes=9)))


class Feed(unittest.TestCase):
    def test_entries_are_read_and_amendments_left_out(self):
        tenq = edgar.parse_latest(FEED.encode("latin-1"), form_type="10-Q")
        self.assertEqual([(f.cik, f.accession, f.form_type) for f in tenq],
                         [("0001045810", NVDA_10Q, "10-Q"),
                          ("0000999999", "0000999999-26-000001", "10-Q")])
        self.assertEqual(tenq[0].company, "NVIDIA CORP")
        self.assertEqual(tenq[0].accepted_at, NOW - timedelta(minutes=4))
        self.assertEqual([f.accession for f in edgar.parse_latest(FEED.encode(), form_type="10-K")],
                         [AAPL_10K], "a 10-K/A is not a 10-K")

    def test_an_entry_that_does_not_parse_is_skipped(self):
        broken = FEED.replace("(0000999999)", "(no cik)")
        self.assertEqual(len(edgar.parse_latest(broken.encode(), form_type="10-Q")), 1)

    def test_pages_are_read_back_to_the_last_good_poll(self):
        from workers import sync_worker

        def page(n, newest):
            return [edgar.LatestFiling("0000000001", f"0000000001-26-{newest - i:06d}", "10-Q",
                                       "X", NOW - timedelta(minutes=newest - i))
                    for i in range(n)]
        calls = []

        def latest(form, *, start=0, count=100):
            calls.append((form, start))
            # page 1 reaches back past the last poll; page 2 is never asked for
            return page(100, 100) if start == 0 else page(100, 400)

        with mock.patch.object(edgar, "latest_filings", latest):
            sync_worker.read_feed(NOW - timedelta(minutes=120))
            self.assertEqual(calls, [("10-K", 0), ("10-K", 100), ("10-Q", 0), ("10-Q", 100)])
            calls.clear()
            sync_worker.read_feed(None)
            self.assertEqual(calls, [("10-K", 0), ("10-Q", 0)], "first poll: a page each")


class Universe(unittest.TestCase):
    def test_share_classes_are_one_company(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(UNIVERSE + "GOOG,0001652044,Alphabet Inc. (Class C),Communication,x,2006\n"
                     "GOOGL,0001652044,Alphabet Inc. (Class A),Communication,x,2014\n")
        self.addCleanup(os.unlink, fh.name)
        u = {t.cik: t for t in load(fh.name)}
        self.assertEqual(len(u), 3)
        self.assertEqual((u["0001652044"].tickers, u["0001652044"].name),
                         (("GOOG", "GOOGL"), "Alphabet Inc."))

    def test_the_shipped_list_is_the_sp500(self):
        u = load()
        self.assertTrue(495 <= len(u) <= 505, len(u))
        self.assertTrue(all(len(t.cik) == 10 and t.cik.isdigit() for t in u))
        self.assertIn("0001045810", {t.cik for t in u})

    def test_the_refresh_reads_wikipedias_table(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import sp500
        html = ("<table id='constituents'><tr><th>Symbol</th><th>Security</th><th>GICS Sector"
                "</th><th>GICS Sub-Industry</th><th>Headquarters Location</th><th>Date added"
                "</th><th>CIK</th><th>Founded</th></tr>"
                "<tr><td><a>BRK.B</a></td><td>Berkshire Hathaway</td><td>Financials</td>"
                "<td>Multi-Sector Holdings</td><td>Omaha</td><td>2010-02-16</td>"
                "<td>1067983</td><td>1839</td></tr></table>")
        [row] = sp500.parse(html, expected=range(1, 2))
        self.assertEqual((row["ticker"], row["cik"], row["sector"]),
                         ("BRK-B", "0001067983", "Financials"))
        with self.assertRaises(ValueError):
            sp500.parse(html)                   # one row is not the S&P 500


class _Edgar:
    """A fixture EDGAR with the feed, and the two-company universe."""

    def setUp(self):
        from test_evidence_e2e import _reset_schema
        _reset_schema()
        os.environ.setdefault("EMBEDDING_PROVIDER", "hashing")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        self.tree = Path(tmp) / "edgar"
        shutil.copytree(EDGAR, self.tree)
        (self.tree / "cgi-bin").mkdir()
        (self.tree / "cgi-bin" / "browse-edgar").write_text(FEED, encoding="latin-1")
        # the 10-Q's document: the 10-K's bytes stand in
        docs = self.tree / "Archives" / "edgar" / "data" / "1045810"
        q = docs / NVDA_10Q.replace("-", "")
        q.mkdir()
        shutil.copy(docs / NVDA_10K.replace("-", "") / "nvda-20250126.htm",
                    q / "nvda-20241027.htm")
        universe = Path(tmp) / "universe.csv"
        universe.write_text(UNIVERSE)
        patch = mock.patch.dict(os.environ, {"SYNC_UNIVERSE": str(universe)})
        patch.start()
        self.addCleanup(patch.stop)
        self.universe = load()
        render = mock.patch("workers.render_worker.render", lambda **kw: [])
        render.start()
        self.addCleanup(render.stop)

    def rows(self, model, *where):
        from sqlalchemy import select
        from parallx_db import session_scope
        with session_scope(DSN) as db:
            return list(db.execute(select(model).where(*where)).scalars())


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Sync(_Edgar, unittest.TestCase):
    def test_backfill_fetches_each_latest_annual_report_once(self):
        from parallx_db import CompanyFetch, Document
        from workers import sync_worker
        with fixture_origin(self.tree):
            self.assertEqual([t.ticker for t in sync_worker.backfill_queue(DSN, self.universe)],
                             ["AAPL", "NVDA"], "the most-asked-about first")
            r = sync_worker.backfill(DSN, self.universe)
        self.assertEqual(r, {"done": 1, "failed": 1, "skipped": 0})
        [doc] = self.rows(Document)
        self.assertEqual((doc.accession, doc.form_type), (NVDA_10K, "10-K"))
        fetches = {f.ticker: f for f in self.rows(CompanyFetch)}
        self.assertEqual({t: (f.source, f.status) for t, f in fetches.items()},
                         {"NVDA": ("backfill", "done"), "AAPL": ("backfill", "failed")})
        self.assertIn("404", fetches["AAPL"].error)
        self.assertEqual(sync_worker.pending_chunks(DSN), fetches["NVDA"].chunks,
                         "parsed now, embedded by the watch loop")
        # done, and a failure not retried at once
        self.assertEqual(sync_worker.backfill_queue(DSN, self.universe), [])

    def test_a_poll_fetches_new_filings_from_tracked_companies_only(self):
        from parallx_db import CompanyFetch, Document, SyncRun
        from workers import sync_worker
        with fixture_origin(self.tree):
            run = sync_worker.poll(DSN, self.universe)
        self.assertEqual((run.seen, run.new, run.fetched, run.failed, run.error),
                         (3, 2, 1, 0 + 1, None))
        [doc] = self.rows(Document)
        self.assertEqual((doc.accession, doc.form_type), (NVDA_10Q, "10-Q"))
        watch = {f.accession: f for f in self.rows(CompanyFetch)}
        self.assertEqual(watch[NVDA_10Q].source, "watch")
        self.assertEqual(watch[NVDA_10Q].status, "done")
        self.assertEqual(watch[AAPL_10K].status, "failed")
        self.assertEqual(len(self.rows(SyncRun)), 1)

    def test_a_failed_filing_is_tried_three_times_and_a_fetched_one_never_again(self):
        from parallx_db import CompanyFetch
        from workers import sync_worker
        with fixture_origin(self.tree):
            runs = [sync_worker.poll(DSN, self.universe) for _ in range(4)]
        self.assertEqual([(r.new, r.fetched, r.failed) for r in runs],
                         [(2, 1, 1), (1, 0, 1), (1, 0, 1), (0, 0, 0)])
        self.assertEqual(len(self.rows(CompanyFetch, CompanyFetch.accession == AAPL_10K)), 3)

    def test_a_company_being_fetched_is_not_fetched_twice(self):
        from parallx_db import CompanyFetch, session_scope
        from workers import sync_worker
        nvda = next(t for t in self.universe if t.ticker == "NVDA")
        with session_scope(DSN) as db:
            db.add(CompanyFetch(cik=nvda.cik, ticker="NVDA", name="NVIDIA CORP",
                                status="running", started_at=datetime.now(timezone.utc)))
        self.assertIsNone(sync_worker.claim(DSN, nvda, source="backfill"),
                          "a search's fetch is under way")
        with session_scope(DSN) as db:
            [row] = db.query(CompanyFetch).all()
            row.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        fid = sync_worker.claim(DSN, nvda, source="backfill")
        self.assertIsNotNone(fid, "a fetch whose process died is failed and replaced")
        stale = self.rows(CompanyFetch, CompanyFetch.id != fid)[0]
        self.assertEqual((stale.status, stale.error), ("failed", "stopped without finishing"))

    def test_the_newest_filing_is_embedded_first(self):
        from parallx_db import Chunk, Document
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.sweep(DSN, self.universe, since=datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.assertEqual({d.accession for d in self.rows(Document)}, {NVDA_10K, NVDA_10Q})
        sync_worker.embed_pass(DSN, max_chunks=3)
        newest = max(self.rows(Document), key=lambda d: d.published_at)
        embedded = self.rows(Chunk, Chunk.embedding.is_not(None))
        self.assertEqual({c.document_id for c in embedded}, {newest.id})

    def test_the_watch_loop_polls_then_embeds_then_backfills(self):
        from parallx_retrieval.embed import EmbeddingError
        from workers import sync_worker
        clock = [NOW]
        w = sync_worker.Watch(DSN, clock=lambda: clock[0], sleep=lambda s: None)
        with fixture_origin(self.tree):
            steps = [w.turn() for _ in range(3)]
            self.assertEqual(steps, ["poll", "embed", "backfill"])
            # AAPL's backfill fails (EDGAR can't serve it); NVDA's 10-K next
            self.assertEqual(w.turn(), "backfill")
            with mock.patch.object(sync_worker, "embed_pass",
                                   side_effect=EmbeddingError("HTTP 429: quota")):
                self.assertEqual(w.turn(), "embed-paused")
                self.assertEqual(w.turn(), "idle", "paused: nothing else to do")
            clock[0] += timedelta(minutes=3)
            self.assertEqual(w.turn(), "embed", "the pause is over")
            self.assertEqual(w.turn(), "idle")
            clock[0] += sync_worker.POLL_EVERY
            self.assertEqual(w.turn(), "poll")

    def test_after_a_long_absence_the_worker_sweeps_first(self):
        from parallx_db import SyncRun, session_scope
        from workers import sync_worker
        away = datetime.now(timezone.utc) - timedelta(hours=3)
        with session_scope(DSN) as db:
            db.add(SyncRun(kind="poll", started_at=away, finished_at=away))
        with fixture_origin(self.tree), \
                mock.patch.object(sync_worker, "sweep", wraps=sync_worker.sweep) as sweep:
            sync_worker.Watch(DSN).catch_up(self.universe)
        sweep.assert_called_once()
        self.assertEqual(sweep.call_args.kwargs["since"], away - sync_worker.OVERLAP)

    def test_status_says_how_complete_and_how_current(self):
        from parallx_db import session_scope
        from parallx_db.sync import status
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.backfill(DSN, self.universe)
            sync_worker.poll(DSN, self.universe)
        with session_scope(DSN) as db:
            s = status(db, [t.cik for t in self.universe])
        self.assertEqual((s["tracked"], s["with_annual_report"], s["searchable"]), (2, 1, 0))
        self.assertGreater(s["pending_chunks"], 0)
        self.assertEqual((s["last_poll"]["fetched"], s["backfill_failures_last_day"]), (1, 1))
        [latest] = s["latest_filings"]
        self.assertEqual((latest["ticker"], latest["form_type"]), ("NVDA", "10-Q"))
        self.assertIsNotNone(latest["minutes_after_filing"])
        sync_worker.embed_pass(DSN, max_chunks=10**6)
        with session_scope(DSN) as db:
            self.assertEqual(status(db, [t.cik for t in self.universe])["searchable"], 1)



@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Api(_Edgar, unittest.TestCase):
    """What the app reads: coverage at /v1/sync, sectors in the company
    list, and search treating a company parsed but not yet embedded as one
    to fetch now, not one to search and find nothing in."""

    def setUp(self):
        super().setUp()
        from test_evidence_e2e import _ApiMixin
        self.api = _ApiMixin()
        self.launched: list[int] = []
        patch = mock.patch("api.routers.fetches.launch", self.launched.append)
        patch.start()
        self.addCleanup(patch.stop)

    def get(self, path, **kw):
        import asyncio

        async def go():
            async with await self.api._client() as c:
                if kw:
                    return await c.post(path, json=kw)
                return await c.get(path)
        return asyncio.run(go())

    def test_sync_status_and_the_company_list(self):
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.backfill(DSN, self.universe)
        r = self.get("/v1/sync")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["tracked"], r.json()["with_annual_report"]), (2, 1))
        [nvda] = self.get("/v1/companies").json()
        self.assertEqual((nvda["ticker"], nvda["sector"], nvda["searchable"]),
                         ("NVDA", "Information Technology", False))
        sync_worker.embed_pass(DSN)
        self.assertTrue(self.get("/v1/companies").json()[0]["searchable"])

    def test_a_company_parsed_but_not_embedded_is_fetched_not_searched(self):
        import json
        from datetime import date
        from parallx_ai.gateway import (Gateway, MemoryStore, internal_organization_id,
                                        set_gateway)
        from parallx_ai.gateway.config import from_dict
        from test_ai_gateway import RAW, Script, ok
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.backfill(DSN, self.universe)
            set_gateway(None)
            nvidia = {"companies": [{"name": "NVIDIA", "ticker": "NVDA"}], "topic": ""}
            set_gateway(Gateway(
                from_dict(RAW, today=date(2026, 11, 26)),
                {"groq": Script("groq", *[ok(text=json.dumps(nvidia))] * 2),
                 "gemini": Script("gemini"), "anthropic": Script("anthropic")},
                MemoryStore(orgs={internal_organization_id(DSN): {}}), sleep=lambda s: None))
            self.addCleanup(set_gateway, None)
            before = self.get("/v1/search", query="nvidia").json()
            sync_worker.embed_pass(DSN)
            after = self.get("/v1/search", query="nvidia").json()
        [c] = before["companies"]
        self.assertEqual((c["covered"], c["fetch"]), (False, "queued"))
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(after["companies"][0]["covered"], True)
        self.assertTrue(after["hits"])


if __name__ == "__main__":
    unittest.main()
