"""The whole pipeline, in one test.

    ingest -> extract -> persist -> GET /v1/companies/NVDA/memory

Every other end-to-end test covers one seam. This one covers all of them at
once, which is the only way to catch the failures that live *between* the
stages — a paragraph id that changes shape between the chunker and the
extractor, a column the worker writes and the API cannot read, an entity that
persists but never surfaces.

Those are exactly the failures the per-stage tests are blind to, because each
one builds its own fixtures rather than consuming the previous stage's output.
`build_for_document` had never actually run before an integration test called
it; this is the shape of test that finds that class of bug.

Only the Claude call is faked, and it is faked *against real ingested data*:
the response cites paragraph ids read back out of the database after ingestion,
so the citation guard is doing real work. A response citing invented ids would
be dropped and the test would fail — which is the point.

Skipped unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import json
import os
import unittest

from test_ingest_e2e import DSN, fixture_origin

TICKER = "NVDA"


# --------------------------------------------------------------- fake Claude
class _Text:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    stop_reason = "end_turn"

    def __init__(self, payload: dict) -> None:
        self.content = [_Text(json.dumps(payload))]


class _Client:
    """Returns entities citing whichever paragraph the chunk actually has.

    The payload is built per request from the text we were sent, so it stays
    honest no matter which chunks the ingest produced.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def messages(self):
        outer = self

        class _M:
            def create(self, **kw):
                outer.calls.append(kw)
                sent = kw["messages"][0]["content"]
                # "[42_7] some text..." — the id the chunker really produced.
                # One chunk per request, so there is exactly one.
                pid = sent.split("]", 1)[0].lstrip("[")
                return _Response({
                    "entities": [
                        {"name": "Blackwell", "entity_type": "product",
                         "confidence": 0.99, "paragraph_id": pid,
                         "quote": sent[:60]},
                        {"name": "AI Infrastructure", "entity_type": "strategy",
                         "confidence": 0.97, "paragraph_id": pid,
                         "quote": sent[:60]},
                        {"name": "Export Controls", "entity_type": "risk",
                         "confidence": 0.95, "paragraph_id": pid,
                         "quote": sent[:60]},
                    ],
                    "relationships": [
                        {"source_name": "AI Infrastructure",
                         "target_name": "Blackwell",
                         "relationship_type": "drives_investment",
                         "confidence": 0.9, "paragraph_id": pid,
                         "quote": sent[:60]},
                    ],
                })
        return _M()


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Pipeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from sqlalchemy import text
        from evident_db import Base, make_engine
        engine = make_engine(DSN)
        Base.metadata.drop_all(engine)
        with engine.begin() as c:
            c.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(engine)

    async def _client(self):
        import httpx
        os.environ["DATABASE_URL"] = DSN
        import api.deps
        api.deps._factory = None                   # rebind to the test DSN
        from api.main import app
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t")

    async def test_a_filing_becomes_readable_memory(self):
        from sqlalchemy import select
        from evident_db import (Chunk, Company, Document, Entity, EntityMention,
                                Relationship, session_scope)
        from workers.ingest_worker import ingest_ticker
        from workers.memory_builder import build_for_document

        # ---------------------------------------------------------- ingest
        with fixture_origin():
            ingested = ingest_ticker(TICKER, limit=1, url=DSN)
        filing = ingested.filings[0]
        self.assertFalse(filing.skipped)
        self.assertGreater(filing.chunks, 0, "ingest produced no chunks")

        # --------------------------------------------------------- extract
        # + persist, through the same worker function production runs
        fake = _Client()
        with session_scope(DSN) as db:
            company = db.execute(select(Company).where(
                Company.ticker == TICKER)).scalar_one()
            document = db.execute(select(Document).where(
                Document.company_id == company.id)).scalar_one()
            stats = build_for_document(db, company_id=company.id,
                                       document=document, client=fake, limit=3)

        self.assertEqual(len(fake.calls), 3,
                         "one request per chunk — a whole filing in one request "
                         "would exceed max_tokens and be rejected entirely")
        self.assertEqual(stats.as_dict()["responses_rejected"], 0)

        # nothing was dropped: the fake cited ids the chunker really produced,
        # so a drop here would mean the ids changed shape between stages
        self.assertEqual(stats.as_dict()["dropped_uncited"], 0,
                         "the extractor rejected ids the chunker had emitted")

        # ---------------------------------------------------------- persist
        with session_scope(DSN) as db:
            entities = {e.slug: e for e in db.execute(select(Entity)).scalars()}
            mentions = list(db.execute(select(EntityMention)).scalars())
            edges = list(db.execute(select(Relationship)).scalars())
            chunk_ids = {c.id for c in db.execute(select(Chunk)).scalars()}

        self.assertLessEqual({"blackwell", "ai_infrastructure", "export_controls"},
                             set(entities))
        self.assertEqual(entities["blackwell"].entity_type, "product")
        self.assertEqual(entities["export_controls"].entity_type, "risk")

        # three chunks, three mentions each, deduplicated per (entity, chunk)
        self.assertEqual(len(mentions), 9)
        for m in mentions:
            self.assertIn(m.chunk_id, chunk_ids,
                          "a mention points at a chunk that does not exist")
            self.assertTrue(m.quote and m.paragraph_id,
                            "a mention without provenance is not evidence")

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].relationship_type, "drives_investment")
        self.assertIsNotNone(edges[0].evidence_chunk_id)

        # ------------------------------------------------------------- read
        async with await self._client() as c:
            r = await c.get(f"/v1/companies/{TICKER.lower()}/memory")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()

        self.assertEqual(body["ticker"], TICKER)
        self.assertEqual(body["document_count"], 1)
        self.assertEqual(body["latest_filing"], str(filing.filed_at))

        # the counts the API reports are the types we actually stored
        self.assertEqual(body["counts"].get("product"), 1)
        self.assertEqual(body["counts"].get("strategy"), 1)
        self.assertEqual(body["counts"].get("risk"), 1)

        top = {e["slug"]: e for e in body["top_entities"]}
        self.assertIn("blackwell", top)
        self.assertEqual(top["blackwell"]["name"], "Blackwell")
        self.assertEqual(top["blackwell"]["mention_count"], 3,
                         "mention_count did not survive the round trip")
        self.assertEqual(top["blackwell"]["first_seen"], str(filing.filed_at))

    async def test_the_entity_endpoint_serves_the_evidence_behind_a_claim(self):
        """The promise the product makes: every claim can show its source.

        A memory card that cannot produce the sentence it came from is the
        failure mode this whole schema exists to prevent, so it is asserted at
        the API boundary rather than in the repository layer.
        """
        from sqlalchemy import select
        from evident_db import Company, Document, session_scope
        from workers.ingest_worker import ingest_ticker
        from workers.memory_builder import build_for_document

        with fixture_origin():
            ingest_ticker(TICKER, limit=1, url=DSN)
        with session_scope(DSN) as db:
            company = db.execute(select(Company).where(
                Company.ticker == TICKER)).scalar_one()
            document = db.execute(select(Document).where(
                Document.company_id == company.id)).scalar_one()
            build_for_document(db, company_id=company.id, document=document,
                               client=_Client(), limit=2)

        async with await self._client() as c:
            r = await c.get(f"/v1/companies/{TICKER.lower()}/entities/blackwell")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()

        self.assertEqual(body["slug"], "blackwell")
        self.assertTrue(body["mentions"], "an entity with no mentions is a claim")
        for m in body["mentions"]:
            self.assertTrue(m["quote"], "a mention with no quote is not evidence")
            self.assertTrue(m["accession"], "a citation must name its filing")
            prov = m["provenance"]
            self.assertIsNotNone(prov["document_id"])
            self.assertTrue(prov["paragraph_id"] or prov["page"],
                            "a citation must locate itself inside the filing")
            self.assertTrue(prov["chunk_hash"],
                            "without the chunk hash the quote cannot be "
                            "re-checked against the filing it came from")


if __name__ == "__main__":
    unittest.main()
