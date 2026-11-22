"""`tools/seed_demo.py`: keyword seeding, and real extraction through the gateway.

The gateway path is exercised with a scripted provider that answers the way a
model would — one entity per request, citing a paragraph id it was actually
sent — so the citation guard, storage and the response cache all run for real.

Skipped unless TEST_DATABASE_URL is set; the key check needs no database.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import seed_demo  # noqa: E402
from test_ingest_e2e import DSN  # noqa: E402

KEYLESS = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}


class KeyCheck(unittest.TestCase):
    def test_gateway_extraction_without_a_key_stops_before_ingesting(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {**KEYLESS, "DATABASE_URL": "postgresql+psycopg://nowhere/none"},
                             clear=True), \
                mock.patch("workers.ingest_worker.ingest_ticker") as ingest, \
                contextlib.redirect_stderr(err):
            code = seed_demo.main(["--extract", "gateway"])
        self.assertEqual(code, 2)
        self.assertIn("GEMINI_API_KEY", err.getvalue())
        ingest.assert_not_called()


class CitesFirstParagraph:
    """A provider that answers like a model: one entity per request, citing
    the first paragraph it was sent, with a quote from it."""

    def __init__(self):
        self.calls = 0

    def complete(self, model, request, *, timeout, params):
        from parallx_ai.gateway import Usage
        from parallx_ai.gateway.types import Completion
        self.calls += 1
        pid, text = re.search(r"^\[([^\]]+)\] (.+)$", request.user, re.M).groups()
        answer = {"entities": [{"name": "Gateway Entity", "entity_type": "risk",
                                "confidence": 0.9, "paragraph_id": pid,
                                "quote": text[:60]}], "relationships": []}
        return Completion(text=json.dumps(answer), stop="complete",
                          usage=Usage(input_tokens=1000, output_tokens=100))


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Seed(unittest.TestCase):
    def setUp(self):
        from test_evidence_e2e import _reset_schema
        _reset_schema()
        env = mock.patch.dict(os.environ, {**KEYLESS, "DATABASE_URL": DSN,
                                           "GEMINI_API_KEY": "test-not-a-key"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        # the renderer drives Chrome; pages are not what this is about
        render = mock.patch("workers.render_worker.render", return_value=[])
        render.start()
        self.addCleanup(render.stop)

    def _gateway(self):
        from parallx_ai.gateway import (Gateway, MemoryStore, internal_organization_id,
                                        set_gateway)
        from parallx_ai.gateway.config import from_dict
        from test_ai_gateway import RAW, Script
        raw = json.loads(json.dumps(RAW))
        raw["limits"].update(max_output_tokens=16_000, monthly_spend_usd=100)
        provider = CitesFirstParagraph()
        store = MemoryStore(orgs={internal_organization_id(DSN): {}})
        set_gateway(Gateway(from_dict(raw, today=date(2026, 11, 21)),
                            {"gemini": provider, "groq": Script("groq"),
                             "anthropic": Script("anthropic")}, store))
        self.addCleanup(set_gateway, None)
        return provider, store

    def _names(self):
        from sqlalchemy import func, select
        from parallx_db import Entity, EntityMention, session_scope
        with session_scope(DSN) as db:
            return dict(db.execute(
                select(Entity.name, func.count(EntityMention.id))
                .join(EntityMention, EntityMention.entity_id == Entity.id)
                .group_by(Entity.name)).all())

    def _seed(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(seed_demo.main(["--corpus", "timeline", *args]), 0)
        return out.getvalue()

    def test_gateway_extraction_replaces_the_keyword_memory_and_reseeds_for_free(self):
        self._seed()
        self.assertIn("Export Controls", self._names())

        provider, store = self._gateway()
        out = self._seed("--extract", "gateway", "--replace")
        chunks = provider.calls
        self.assertEqual(chunks, 94, "one request per chunk of the three filings")
        self.assertEqual(list(self._names()), ["Gateway Entity"],
                         "--replace leaves no keyword entity behind")
        self.assertEqual(self._names()["Gateway Entity"], chunks)
        self.assertIn("0 rejected", out)
        self.assertRegex(out, r"extraction cost \$0\.\d{4}")
        self.assertEqual({r.status for r in store.rows}, {"success"})

        self._seed("--extract", "gateway", "--replace")
        self.assertEqual(provider.calls, chunks, "the second seed paid for nothing")
        self.assertEqual(self._names(), {"Gateway Entity": chunks})
        self.assertEqual(sum(r.status == "cached" for r in store.rows), chunks)

    def test_without_replace_the_two_extractors_mix(self):
        """Why --replace exists."""
        self._seed()
        self._gateway()
        self._seed("--extract", "gateway")
        names = self._names()
        self.assertIn("Gateway Entity", names)
        self.assertIn("Export Controls", names)


if __name__ == "__main__":
    unittest.main()
