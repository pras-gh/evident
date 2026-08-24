"""Ingestion end-to-end against a fixture EDGAR origin.

The fetch layer's origins are configurable, so this exercises the real
`ingest_ticker` path — ticker resolution, submissions lookup, document fetch,
HTML parse, chunking, and the database writes — with only the origin swapped.
That is the same reason the override exists in production: SEC blocks whole IP
ranges at the edge, and a hard-coded origin makes those environments untestable.

Skipped unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import os
import socketserver
import threading
import unittest
from pathlib import Path

DSN = os.environ.get("TEST_DATABASE_URL")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar"


@contextlib.contextmanager
def fixture_origin():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(FIXTURES))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        previous = {k: os.environ.get(k) for k in
                    ("SEC_WWW_URL", "SEC_DATA_URL", "SEC_ARCHIVES_URL",
                     "SEC_USER_AGENT")}
        os.environ.update({
            "SEC_WWW_URL": base, "SEC_DATA_URL": base,
            "SEC_ARCHIVES_URL": f"{base}/Archives/edgar/data",
            "SEC_USER_AGENT": "Evident test",
        })
        # the ticker map is cached for the process lifetime
        import evident_parser.edgar as edgar
        edgar._TICKER_MAP = None
        edgar.BASE = base
        edgar.WWW = base
        edgar.ARCHIVES = f"{base}/Archives/edgar/data"
        try:
            yield base
        finally:
            httpd.shutdown()
            for k, v in previous.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Ingest(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import text
        from evident_db import Base, make_engine
        engine = make_engine(DSN)
        with engine.begin() as c:
            c.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(engine)
        with engine.begin() as c:
            names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
            c.execute(text(f"truncate {names} restart identity cascade"))
        engine.dispose()

    def test_ingests_a_filing_end_to_end(self):
        from workers.ingest_worker import ingest_ticker

        with fixture_origin():
            result = ingest_ticker("NVDA", limit=1, url=DSN)

        self.assertEqual(result.cik, "0001045810")
        self.assertEqual(result.company, "NVIDIA CORP")
        filing = result.filings[0]
        self.assertIsNone(filing.error)
        self.assertFalse(filing.skipped)
        self.assertEqual(filing.form_type, "10-K")
        self.assertGreater(filing.chunks, 0)
        self.assertGreater(filing.sections, 0)
        self.assertGreater(filing.pages, 1, "page-break markers were not counted")

    def test_preserves_page_numbers_and_section_titles(self):
        """The requirement the whole citation promise rests on."""
        from sqlalchemy import select
        from evident_db import Chunk, session_scope
        from workers.ingest_worker import ingest_ticker

        with fixture_origin():
            ingest_ticker("NVDA", limit=1, url=DSN)

        with session_scope(DSN) as db:
            chunks = list(db.execute(select(Chunk).order_by(Chunk.ordinal)).scalars())

        self.assertTrue(all(c.page_number for c in chunks),
                        "a chunk landed without a page number")
        self.assertTrue(all(c.section_title for c in chunks),
                        "a chunk landed without a section title")
        titles = {c.section_title for c in chunks}
        self.assertTrue(any("Risk Factors" in t for t in titles))
        # page numbers must advance across the page-break markers
        self.assertGreater(max(c.page_number for c in chunks), 1)

    def test_reingesting_unchanged_bytes_is_skipped(self):
        from workers.ingest_worker import ingest_ticker

        with fixture_origin():
            ingest_ticker("NVDA", limit=1, url=DSN)
            second = ingest_ticker("NVDA", limit=1, url=DSN)

        self.assertTrue(second.filings[0].skipped)
        self.assertEqual(second.chunks_written, 0)

    def test_unknown_ticker_raises_lookup_error(self):
        from workers.ingest_worker import ingest_ticker
        with fixture_origin(), self.assertRaises(LookupError):
            ingest_ticker("ZZZZ", limit=1, url=DSN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
