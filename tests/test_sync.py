"""The S&P 500 from EDGAR's APIs, kept current (`workers/sync_worker.py`).

EDGAR is a fixture tree served locally. NVIDIA's filing list, its Company
Facts (synthetic values, real shapes), and its documents; a latest-filings
feed listing a new NVIDIA 10-Q and 8-K, a 10-Q from a company nobody tracks,
and an amendment (not watched). Apple is tracked but EDGAR cannot serve it.
The filing list is written twice: before the new filings (what a backfill
sees) and after (what a poll then finds). Database tests skip unless
TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from parallx_parser import edgar
from parallx_parser.facts import BY_KEY, facts, statement
from parallx_parser.universe import load

from test_ingest_e2e import DSN, fixture_origin

ROOT = Path(__file__).resolve().parents[1]
EDGAR = ROOT / "tests" / "fixtures" / "edgar-real"
NVDA = "0001045810"
NVDA_10K, NVDA_10Q = "0001045810-25-000023", "0001045810-24-000316"
NEW_10Q, NEW_8K, FORM_4 = "0001045810-26-000101", "0001045810-26-000100", "0001045810-26-000090"
OLD_10K = "0001045810-20-000010"

UNIVERSE = """# test universe
ticker,cik,name,sector,sub_industry,date_added
AAPL,0000320193,Apple Inc.,Information Technology,Hardware,1982-11-30
NVDA,0001045810,Nvidia,Information Technology,Semiconductors,2001-11-30
"""

NOW = datetime.now(timezone.utc).replace(microsecond=0)


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


FEED = feed(("10-Q", "NVIDIA CORP", NVDA, NEW_10Q, NOW - timedelta(minutes=4)),
            ("8-K", "NVIDIA CORP", NVDA, NEW_8K, NOW - timedelta(minutes=6)),
            ("10-Q", "Acme Unlisted Inc", "0000999999", "0000999999-26-000001",
             NOW - timedelta(minutes=7)),
            ("10-K/A", "NVIDIA CORP", NVDA, "0001045810-26-000099",
             NOW - timedelta(minutes=8)))


def submission(*, with_new: bool) -> dict:
    rows = [
        (NVDA_10K, "10-K", "2025-02-26", "2025-02-26T21:05:12.000Z", "2025-01-26",
         "nvda-20250126.htm", ""),
        (NVDA_10Q, "10-Q", "2024-11-20", "2024-11-20T16:31:02.000Z", "2024-10-27",
         "nvda-20241027.htm", ""),
        (FORM_4, "4", "2026-03-01", "2026-03-01T22:00:00.000Z", "", "form4.xml", ""),
    ]
    if with_new:
        rows = [(NEW_10Q, "10-Q", NOW.date().isoformat(),
                 (NOW - timedelta(minutes=4)).isoformat(), "2026-10-26", "nvda-20261026.htm", ""),
                (NEW_8K, "8-K", NOW.date().isoformat(), (NOW - timedelta(minutes=6)).isoformat(),
                 "", "nvda-8k.htm", "2.02,9.01")] + rows
    cols = ["accessionNumber", "form", "filingDate", "acceptanceDateTime", "reportDate",
            "primaryDocument", "items"]
    return {"cik": "1045810", "name": "NVIDIA CORP", "tickers": ["NVDA"], "sic": "3674",
            "filings": {"recent": {c: [r[i] for r in rows] for i, c in enumerate(cols)},
                        "files": [{"name": "CIK0001045810-submissions-001.json"}]}}


OLD_PAGE = {"accessionNumber": [OLD_10K], "form": ["10-K"], "filingDate": ["2020-02-20"],
            "acceptanceDateTime": ["2020-02-20T21:00:00.000Z"], "reportDate": ["2020-01-26"],
            "primaryDocument": ["nvda-2020.htm"]}


def fact(start, end, val, accn, fy, fp, form, filed, frame):
    out = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form,
           "filed": filed}
    if start:
        out["start"] = start
    if frame:
        out["frame"] = frame
    return out


Q1, Q2 = "0001045810-24-000124", "0001045810-24-000264"
FACTS = {"cik": 1045810, "entityName": "NVIDIA CORP", "facts": {
    "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        fact(None, "2025-02-19", 24_400_000_000, NVDA_10K, 2025, "FY", "10-K", "2025-02-26",
             "CY2025Q1I")]}}},
    "us-gaap": {
        "SalesRevenueNet": {"units": {"USD": [
            fact("2022-01-31", "2023-01-29", 27_000_000_000, "0001045810-23-000017", 2023, "FY",
                 "10-K", "2023-02-24", "CY2022")]}},
        "Revenues": {"units": {"USD": [
            fact("2023-01-30", "2024-01-28", 60_000_000_000, "0001045810-24-000029", 2024, "FY",
                 "10-K", "2024-02-21", "CY2023"),
            fact("2024-01-29", "2025-01-26", 130_000_000_000, NVDA_10K, 2025, "FY", "10-K",
                 "2025-02-26", "CY2024"),
            fact("2024-01-29", "2024-04-28", 26_000_000_000, Q1, 2025, "Q1", "10-Q",
                 "2024-05-29", "CY2024Q1"),
            fact("2024-04-29", "2024-07-28", 30_000_000_000, Q2, 2025, "Q2", "10-Q",
                 "2024-08-28", "CY2024Q2"),
            fact("2024-07-29", "2024-10-27", 35_000_000_000, NVDA_10Q, 2025, "Q3", "10-Q",
                 "2024-11-20", "CY2024Q3"),
            # a press release's number, and a comparative EDGAR did not frame
            fact("2024-10-28", "2025-01-26", 39_500_000_000, "0001045810-25-000001", 2025, "Q4",
                 "8-K", "2025-02-26", "CY2024Q4"),
            fact("2024-07-29", "2024-10-27", 35_000_000_000, NEW_10Q, 2026, "Q3", "10-Q",
                 "2025-11-19", None),
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            fact("2024-01-29", "2025-01-26", 70_000_000_000, NVDA_10K, 2025, "FY", "10-K",
                 "2025-02-26", "CY2024"),
            fact("2024-01-29", "2024-04-28", 15_000_000_000, Q1, 2025, "Q1", "10-Q",
                 "2024-05-29", "CY2024Q1"),
            fact("2024-04-29", "2024-07-28", 16_000_000_000, Q2, 2025, "Q2", "10-Q",
                 "2024-08-28", "CY2024Q2"),
            fact("2024-07-29", "2024-10-27", 19_000_000_000, NVDA_10Q, 2025, "Q3", "10-Q",
                 "2024-11-20", "CY2024Q3"),
        ]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            fact("2024-01-29", "2025-01-26", 2.90, NVDA_10K, 2025, "FY", "10-K", "2025-02-26",
                 "CY2024"),
            fact("2024-07-29", "2024-10-27", 0.78, NVDA_10Q, 2025, "Q3", "10-Q", "2024-11-20",
                 "CY2024Q3"),
        ]}},
        "Assets": {"units": {"USD": [
            fact(None, "2024-01-28", 65_000_000_000, "0001045810-24-000029", 2024, "FY", "10-K",
                 "2024-02-21", "CY2023Q4I"),
            fact(None, "2025-01-26", 110_000_000_000, NVDA_10K, 2025, "FY", "10-K",
                 "2025-02-26", "CY2024Q4I"),
        ]}},
        "SomethingNobodyAsked": {"units": {"USD": [
            fact(None, "2025-01-26", 1, NVDA_10K, 2025, "FY", "10-K", "2025-02-26",
                 "CY2024Q4I")]}},
    }}}


def stored(payload=FACTS):
    """Facts as the database hands them back: objects with Fact's attributes."""
    return [SimpleNamespace(**{k: getattr(f, k) for k in f.__slots__}) for f in facts(payload)]


# ------------------------------------------------------------ no database


class Feed(unittest.TestCase):
    def test_entries_are_read_and_amendments_left_out(self):
        tenq = edgar.parse_latest(FEED.encode("latin-1"), form_type="10-Q")
        self.assertEqual([(f.cik, f.accession) for f in tenq],
                         [(NVDA, NEW_10Q), ("0000999999", "0000999999-26-000001")])
        self.assertEqual((tenq[0].company, tenq[0].accepted_at),
                         ("NVIDIA CORP", NOW - timedelta(minutes=4)))
        self.assertEqual(edgar.parse_latest(FEED.encode(), form_type="10-K"), [],
                         "a 10-K/A is not a 10-K")

    def test_an_entry_that_does_not_parse_is_skipped(self):
        broken = FEED.replace("(0000999999)", "(no cik)")
        self.assertEqual(len(edgar.parse_latest(broken.encode(), form_type="10-Q")), 1)

    def test_pages_are_read_back_to_the_last_good_poll(self):
        from workers import sync_worker

        def page(newest):
            return [edgar.LatestFiling("0000000001", f"0000000001-26-{newest - i:06d}", "10-Q",
                                       "X", NOW - timedelta(minutes=newest - i))
                    for i in range(100)]
        calls = []

        def latest(form, *, start=0, count=100):
            calls.append((form, start))
            return page(100) if start == 0 else page(400)   # page 2 reaches past it

        with mock.patch.object(edgar, "latest_filings", latest):
            sync_worker.read_feed(NOW - timedelta(minutes=120))
            self.assertEqual(calls, [(f, s) for f in sync_worker.WATCH_FORMS for s in (0, 100)])
            calls.clear()
            sync_worker.read_feed(None)
            self.assertEqual(calls, [(f, 0) for f in sync_worker.WATCH_FORMS],
                             "first poll: a page each")


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
        self.assertIn(NVDA, {t.cik for t in u})

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


class Statements(unittest.TestCase):
    def test_kept_framed_periodic_values_of_the_statement_lines(self):
        kept = list(facts(FACTS))
        self.assertTrue(all(f.frame for f in kept))
        self.assertNotIn("8-K", {f.form for f in kept}, "a press release is not a statement")
        self.assertNotIn(NEW_10Q, {f.accession for f in kept}, "unframed: EDGAR chose another")
        self.assertNotIn("us-gaap:SomethingNobodyAsked", {f.concept for f in kept})
        self.assertEqual(len(kept), 15)

    def test_annual_a_column_per_fiscal_year_and_the_old_revenue_tag(self):
        st = statement(stored(), period="annual")
        self.assertEqual([c.label for c in st.columns], ["FY2023", "FY2024", "FY2025"])
        rev = st.rows["revenue"]
        self.assertEqual([c.value for c in rev],
                         [Decimal(27_000_000_000), Decimal(60_000_000_000),
                          Decimal(130_000_000_000)])
        self.assertEqual(rev[0].concept, "us-gaap:SalesRevenueNet")
        self.assertEqual((rev[2].accession, rev[2].form), (NVDA_10K, "10-K"))
        self.assertEqual([c and c.value for c in st.rows["total_assets"]],
                         [None, Decimal(65_000_000_000), Decimal(110_000_000_000)])
        # dated on the cover, three weeks after the year ended
        self.assertEqual(st.rows["shares_outstanding"][2].value, Decimal(24_400_000_000))

    def test_quarterly_derives_q4_only_for_what_adds_up(self):
        st = statement(stored(), period="quarterly", periods=8)
        self.assertEqual([c.label for c in st.columns],
                         ["Q1 FY2025", "Q2 FY2025", "Q3 FY2025", "Q4 FY2025"])
        rev = st.rows["revenue"]
        self.assertEqual([c.value for c in rev],
                         [Decimal(v) for v in (26e9, 30e9, 35e9, 39e9)])
        self.assertEqual([c.derived for c in rev], [False, False, False, True])
        self.assertEqual(rev[3].accession, NVDA_10K, "a derived Q4 cites the 10-K")
        self.assertIsNone(st.rows["eps_diluted"][3], "EPS does not add up across quarters")

    def test_a_company_with_no_facts_has_an_empty_statement(self):
        st = statement([], period="annual")
        self.assertEqual((st.columns, st.rows["revenue"]), ([], []))
        self.assertEqual(BY_KEY["revenue"].unit, "USD")


class Alerts(unittest.TestCase):
    def filing(self, form="8-K", items="2.02,9.01", report=None):
        from workers.alerts import NewFiling
        return NewFiling("NVDA", "Nvidia", NVDA, NEW_8K, form, NOW, report, items)

    def test_a_filing_in_words(self):
        from workers.alerts import text
        self.assertEqual(text(self.filing()),
                         "NVDA filed an 8-K (current report): results of operations")
        self.assertEqual(text(self.filing("10-Q", None, date(2026, 10, 26))),
                         "NVDA filed a 10-Q (quarterly report) for the period ended 2026-10-26")

    def test_a_webhook_gets_text_and_fields_and_a_failed_one_is_not_counted(self):
        from workers import alerts
        posted = []
        with mock.patch.dict(os.environ, {"ALERT_WEBHOOK_URL": "https://hooks.example/x"}):
            sent = alerts.send([self.filing()], post=lambda url, body: posted.append(body))
            self.assertEqual(sent, [NEW_8K])
            self.assertIn("results of operations", posted[0]["text"])
            self.assertEqual(posted[0]["filing"]["accession"], NEW_8K)

            def down(url, body):
                raise ConnectionError("refused")
            self.assertEqual(alerts.send([self.filing()], post=down), [])

    def test_only_the_alerting_forms(self):
        from workers import alerts
        with mock.patch.dict(os.environ, {"ALERT_FORMS": "10-K"}):
            self.assertEqual(alerts.send([self.filing()]), [])


# ------------------------------------------------------------ database


class _Edgar:
    """A fixture EDGAR, and the two-company universe."""

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
        facts_dir = self.tree / "api" / "xbrl" / "companyfacts"
        facts_dir.mkdir(parents=True)
        (facts_dir / f"CIK{NVDA}.json").write_text(json.dumps(FACTS))
        (self.tree / "submissions" / "CIK0001045810-submissions-001.json").write_text(
            json.dumps(OLD_PAGE))
        self.publish(with_new=False)
        # documents: the 10-K's bytes stand in for each 10-Q
        docs = self.tree / "Archives" / "edgar" / "data" / "1045810"
        tenk = docs / NVDA_10K.replace("-", "") / "nvda-20250126.htm"
        for acc, name in ((NVDA_10Q, "nvda-20241027.htm"), (NEW_10Q, "nvda-20261026.htm")):
            (docs / acc.replace("-", "")).mkdir()
            shutil.copy(tenk, docs / acc.replace("-", "") / name)
        universe = Path(tmp) / "universe.csv"
        universe.write_text(UNIVERSE)
        for patch in (mock.patch.dict(os.environ, {"SYNC_UNIVERSE": str(universe)}),
                      mock.patch("workers.render_worker.render", lambda **kw: [])):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("ALERT_WEBHOOK_URL", None)
        self.universe = load()
        self.nvda = next(t for t in self.universe if t.ticker == "NVDA")

    def publish(self, *, with_new: bool):
        """EDGAR's filing list for NVIDIA, before or after the new filings."""
        (self.tree / "submissions" / f"CIK{NVDA}.json").write_text(
            json.dumps(submission(with_new=with_new)))

    def rows(self, model, *where):
        from sqlalchemy import select
        from parallx_db import session_scope
        with session_scope(DSN) as db:
            return list(db.execute(select(model).where(*where)).scalars())


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Sync(_Edgar, unittest.TestCase):
    def test_backfill_indexes_filings_and_statements_and_downloads_nothing(self):
        from parallx_db import Company, Document, Filing, FinancialFact
        from workers import sync_worker
        with fixture_origin(self.tree):
            self.assertEqual([t.ticker for t in sync_worker.backfill_queue(DSN, self.universe)],
                             ["AAPL", "NVDA"], "the most-asked-about first")
            r = sync_worker.backfill(DSN, self.universe)
        self.assertEqual(r, {"indexed": 1, "failed": 1, "documents": 0})
        filings = {f.accession: f for f in self.rows(Filing)}
        self.assertEqual(set(filings), {NVDA_10K, NVDA_10Q}, "a Form 4 is not indexed")
        self.assertTrue(all(f.alerted_at is None for f in filings.values()), "history, not news")
        self.assertEqual(len(self.rows(FinancialFact)), 15)
        [nvda] = self.rows(Company)
        self.assertIsNotNone(nvda.filings_indexed_at and nvda.facts_loaded_at)
        self.assertEqual(self.rows(Document), [], "text is downloaded on demand")
        with fixture_origin(self.tree):
            self.assertEqual([t.ticker for t in sync_worker.backfill_queue(DSN, self.universe)],
                             ["AAPL"], "Apple is tried again; NVIDIA is done")

    def test_backfill_can_download_the_first_few_annual_reports(self):
        from parallx_db import Document
        from workers import sync_worker
        with fixture_origin(self.tree):
            r = sync_worker.backfill(DSN, self.universe, documents=2)
        self.assertEqual(r["documents"], 1, "Apple's fails; NVIDIA's 10-K is downloaded")
        self.assertEqual([d.accession for d in self.rows(Document)], [NVDA_10K])

    def test_a_poll_indexes_new_filings_and_alerts_once(self):
        from parallx_db import Filing, SyncRun
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.backfill(DSN, self.universe)
            self.publish(with_new=True)
            with self.assertLogs("parallx.alerts", "INFO") as logs:
                run = sync_worker.poll(DSN, self.universe)
            again = sync_worker.poll(DSN, self.universe)
        self.assertEqual((run.seen, run.new, run.fetched, run.failed, run.error),
                         (2, 2, 0, 0, None))
        alerted = {f.accession for f in self.rows(Filing, Filing.alerted_at.is_not(None))}
        self.assertEqual(alerted, {NEW_10Q, NEW_8K})
        self.assertTrue(any("NVDA filed an 8-K (current report): results of operations" in m
                            for m in logs.output))
        self.assertEqual((again.new, again.seen), (0, 2), "indexed: nothing new, no alert")
        self.assertEqual(len(self.rows(SyncRun)), 2)

    def test_a_first_indexing_alerts_only_what_was_just_filed(self):
        from parallx_db import Filing
        from workers import sync_worker
        self.publish(with_new=True)
        with fixture_origin(self.tree):
            sync_worker.poll(DSN, self.universe)          # NVIDIA, never backfilled
        self.assertEqual(len(self.rows(Filing)), 4)
        self.assertEqual({f.accession for f in self.rows(Filing, Filing.alerted_at.is_not(None))},
                         {NEW_10Q, NEW_8K})

    def test_a_company_whose_filings_are_loaded_gets_new_ones_downloaded(self):
        from parallx_db import CompanyFetch, Document
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.backfill(DSN, self.universe)
            self.assertEqual(sync_worker.fetch(DSN, self.nvda, source="search"), "done")
            self.publish(with_new=True)
            run = sync_worker.poll(DSN, self.universe)
        self.assertEqual(run.fetched, 1)
        self.assertEqual({d.accession for d in self.rows(Document)}, {NVDA_10K, NEW_10Q},
                         "the 10-Q, not the 8-K")
        [watch] = self.rows(CompanyFetch, CompanyFetch.source == "watch")
        self.assertEqual((watch.accession, watch.status), (NEW_10Q, "done"))

    def test_a_failed_download_is_tried_again_at_the_next_poll(self):
        from parallx_db import Document
        from workers import sync_worker
        doc = (self.tree / "Archives" / "edgar" / "data" / "1045810" /
               NEW_10Q.replace("-", "") / "nvda-20261026.htm")
        kept = doc.read_bytes()
        with fixture_origin(self.tree):
            sync_worker.backfill(DSN, self.universe)
            sync_worker.fetch(DSN, self.nvda, source="search")
            self.publish(with_new=True)
            doc.unlink()                                   # not there yet
            first = sync_worker.poll(DSN, self.universe)
            doc.write_bytes(kept)
            second = sync_worker.poll(DSN, self.universe)
        self.assertEqual((first.fetched, second.fetched), (0, 1))
        self.assertIn(NEW_10Q, {d.accession for d in self.rows(Document)})

    def test_a_company_being_fetched_is_not_fetched_twice(self):
        from parallx_db import CompanyFetch, session_scope
        from workers import sync_worker
        with session_scope(DSN) as db:
            db.add(CompanyFetch(cik=NVDA, ticker="NVDA", name="NVIDIA CORP",
                                status="running", started_at=datetime.now(timezone.utc)))
        self.assertIsNone(sync_worker.claim(DSN, self.nvda, source="backfill"),
                          "a search's fetch is under way")
        with session_scope(DSN) as db:
            [row] = db.query(CompanyFetch).all()
            row.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        fid = sync_worker.claim(DSN, self.nvda, source="backfill")
        self.assertIsNotNone(fid, "a fetch whose process died is failed and replaced")
        stale = self.rows(CompanyFetch, CompanyFetch.id != fid)[0]
        self.assertEqual((stale.status, stale.error), ("failed", "stopped without finishing"))

    def test_the_newest_filing_is_embedded_first(self):
        from parallx_db import Chunk, Document
        from workers import sync_worker
        with fixture_origin(self.tree):
            for acc in (NVDA_10Q, NVDA_10K):
                sync_worker.fetch(DSN, self.nvda, source="search", accession=acc)
        sync_worker.embed_pass(DSN, max_chunks=3)
        newest = max(self.rows(Document), key=lambda d: d.published_at)
        embedded = self.rows(Chunk, Chunk.embedding.is_not(None))
        self.assertEqual({c.document_id for c in embedded}, {newest.id})

    def test_the_watch_loop(self):
        from parallx_retrieval.embed import EmbeddingError
        from workers import sync_worker
        clock = [NOW]
        w = sync_worker.Watch(DSN, clock=lambda: clock[0], sleep=lambda s: None)
        self.publish(with_new=True)
        with fixture_origin(self.tree):
            self.assertEqual(w.turn(), "poll")            # NVIDIA, from the feed
            self.assertEqual(w.turn(), "backfill")        # Apple: EDGAR can't serve it
            self.assertEqual(w.turn(), "idle", "Apple waits INDEX_RETRY")
            sync_worker.fetch(DSN, self.nvda, source="search")   # a search loads NVIDIA
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
        from parallx_db import Filing, SyncRun, session_scope
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.backfill(DSN, self.universe)
        away = datetime.now(timezone.utc) - timedelta(hours=3)
        with session_scope(DSN) as db:
            db.add(SyncRun(kind="poll", started_at=away, finished_at=away))
        self.publish(with_new=True)
        with fixture_origin(self.tree), \
                mock.patch.object(sync_worker, "sweep", wraps=sync_worker.sweep) as sweep:
            sync_worker.Watch(DSN).catch_up(self.universe)
        self.assertEqual(sweep.call_args.kwargs["since"], away - sync_worker.OVERLAP)
        self.assertEqual({f.accession for f in self.rows(Filing, Filing.alerted_at.is_not(None))},
                         {NEW_10Q, NEW_8K}, "what was filed while away alerts")

    def test_status_says_how_complete_and_how_current(self):
        from parallx_db import session_scope
        from parallx_db.sync import status
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.backfill(DSN, self.universe)
            sync_worker.fetch(DSN, self.nvda, source="search")
            self.publish(with_new=True)
            sync_worker.poll(DSN, self.universe)
        with session_scope(DSN) as db:
            s = status(db, [t.cik for t in self.universe])
        self.assertEqual((s["tracked"], s["indexed"], s["with_financials"], s["loaded"],
                          s["searchable"]), (2, 1, 1, 1, 0))
        self.assertGreater(s["pending_chunks"], 0)
        self.assertEqual(s["last_poll"]["new"], 2)
        newest = s["latest_filings"][0]
        self.assertEqual((newest["accession"], newest["alerted"], newest["loaded"]),
                         (NEW_10Q, True, True))
        self.assertIsNotNone(newest["minutes_after_filing"])
        sync_worker.embed_pass(DSN, max_chunks=10**6)
        with session_scope(DSN) as db:
            self.assertEqual(status(db, [t.cik for t in self.universe])["searchable"], 1)


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Bulk(_Edgar, unittest.TestCase):
    def test_the_zips_load_whole_histories_without_the_api(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import sec_bulk
        from parallx_db import Filing, FinancialFact
        bulk = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, bulk)
        with zipfile.ZipFile(bulk / "submissions.zip", "w") as z:
            z.writestr(f"CIK{NVDA}.json", json.dumps(submission(with_new=False)))
            z.writestr("CIK0001045810-submissions-001.json", json.dumps(OLD_PAGE))
        with zipfile.ZipFile(bulk / "companyfacts.zip", "w") as z:
            z.writestr(f"CIK{NVDA}.json", json.dumps(FACTS))
        # no fixture server: any request to EDGAR would fail
        r = sec_bulk.load(bulk, only={"NVDA"}, url=DSN)
        self.assertEqual(r, {"companies": 1, "filings": 3, "facts": 15, "missing": 0})
        self.assertIn(OLD_10K, {f.accession for f in self.rows(Filing)}, "the older page too")
        self.assertEqual(len(self.rows(FinancialFact)), 15)


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Api(_Edgar, unittest.TestCase):
    """What the app reads."""

    def setUp(self):
        super().setUp()
        from test_evidence_e2e import _ApiMixin
        self.api = _ApiMixin()
        self.launched: list[int] = []
        patch = mock.patch("api.routers.fetches.launch", self.launched.append)
        patch.start()
        self.addCleanup(patch.stop)

    def call(self, path, **kw):
        import asyncio

        async def go():
            async with await self.api._client() as c:
                return await (c.post(path, json=kw) if kw else c.get(path))
        r = asyncio.run(go())
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def synced(self):
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.backfill(DSN, self.universe)
            self.publish(with_new=True)
            sync_worker.poll(DSN, self.universe)

    def test_the_latest_filings(self):
        self.synced()
        [eight_k] = self.call("/v1/filings/latest?forms=8-K")
        self.assertEqual((eight_k["ticker"], eight_k["about"], eight_k["alerted"],
                          eight_k["loaded"]), ("NVDA", "results of operations", True, False))
        self.assertEqual(eight_k["url"], "https://www.sec.gov/Archives/edgar/data/1045810/"
                                         "000104581026000100/nvda-8k.htm")
        recent = self.call("/v1/filings/latest?days=14")
        self.assertEqual([f["accession"] for f in recent], [NEW_10Q, NEW_8K],
                         "news, not the history a backfill indexed")
        mine = self.call("/v1/companies/NVDA/filings")
        self.assertEqual([f["accession"] for f in mine], [NEW_10Q, NEW_8K, NVDA_10K, NVDA_10Q])

    def test_financial_statements(self):
        self.synced()
        annual = self.call("/v1/companies/NVDA/financials")
        self.assertEqual([c["label"] for c in annual["columns"]], ["FY2023", "FY2024", "FY2025"])
        rev = next(r for r in annual["rows"] if r["key"] == "revenue")
        self.assertEqual(rev["cells"][2]["value"], 130e9)
        self.assertTrue(rev["cells"][2]["url"].endswith("/000104581025000023/nvda-20250126.htm"))
        self.assertNotIn("gross_profit", {r["key"] for r in annual["rows"]},
                         "a line never filed is left out")
        q = self.call("/v1/companies/NVDA/financials?period=quarterly")
        qrev = next(r for r in q["rows"] if r["key"] == "revenue")
        self.assertEqual((qrev["cells"][3]["value"], qrev["cells"][3]["derived"]), (39e9, True))

    def test_sync_status_and_the_company_list(self):
        self.synced()
        s = self.call("/v1/sync")
        self.assertEqual((s["tracked"], s["indexed"], s["with_financials"]), (2, 1, 1))
        [nvda] = self.call("/v1/companies")
        self.assertEqual((nvda["ticker"], nvda["sector"], nvda["display_name"],
                          nvda["searchable"], nvda["document_count"]),
                         ("NVDA", "Information Technology", "Nvidia", False, 0))

    def test_a_company_parsed_but_not_embedded_is_fetched_not_searched(self):
        from parallx_ai.gateway import (Gateway, MemoryStore, internal_organization_id,
                                        set_gateway)
        from parallx_ai.gateway.config import from_dict
        from test_ai_gateway import RAW, Script, ok
        from workers import sync_worker
        with fixture_origin(self.tree):
            sync_worker.fetch(DSN, self.nvda, source="search")
            set_gateway(None)
            nvidia = {"companies": [{"name": "NVIDIA", "ticker": "NVDA"}], "topic": ""}
            set_gateway(Gateway(
                from_dict(RAW, today=date(2026, 11, 26)),
                {"groq": Script("groq", *[ok(text=json.dumps(nvidia))] * 2),
                 "gemini": Script("gemini"), "anthropic": Script("anthropic")},
                MemoryStore(orgs={internal_organization_id(DSN): {}}), sleep=lambda s: None))
            self.addCleanup(set_gateway, None)
            before = self.call("/v1/search", query="nvidia")
            sync_worker.embed_pass(DSN)
            after = self.call("/v1/search", query="nvidia")
        [c] = before["companies"]
        self.assertEqual((c["covered"], c["fetch"]), (False, "queued"))
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(after["companies"][0]["covered"], True)
        self.assertTrue(after["hits"])


if __name__ == "__main__":
    unittest.main()
