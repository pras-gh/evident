"""End-to-end against a real PostgreSQL + pgvector.

Skipped unless TEST_DATABASE_URL is set, so the stdlib-only suite still runs on
a bare checkout. When it does run it exercises the parts that unit tests
cannot honestly cover: that the upserts really do update instead of duplicating,
that pgvector stores and searches what we wrote, and that the API returns a
citation with every claim.
"""
from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone

DSN = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class MemoryEngine(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        from evident_db import Base, make_engine
        cls.engine = make_engine(DSN)
        Base.metadata.drop_all(cls.engine)
        with cls.engine.begin() as c:
            from sqlalchemy import text
            c.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(cls.engine)

    def setUp(self):
        # Each test starts from a clean database. setUp runs per test, so
        # without this the second seed correctly reports "content unchanged"
        # and every later assertion is testing the wrong state.
        from sqlalchemy import text
        from evident_db import Base
        with self.engine.begin() as c:
            names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
            c.execute(text(f"truncate {names} restart identity cascade"))

        from evident_db import session_scope
        from evident_db.repositories import (replace_chunks, upsert_company,
                                             upsert_document)
        from evident_parser.models import chunk_hash, content_id

        with session_scope(DSN) as db:
            company = upsert_company(db, cik="1045810", name="NVIDIA Corp",
                                     ticker="NVDA")
            db.flush()
            self.company_id = company.id
            doc, is_new = upsert_document(
                db, company_id=company.id, accession="0001045810-25-000023",
                form_type="10-K", filed_at=date(2025, 2, 26),
                published_at=datetime(2025, 2, 26, 21, 5, tzinfo=timezone.utc),
                source_url="https://example.invalid/f.htm", source_format="html",
                content_sha256="a" * 64, fiscal_period="FY2025", page_count=120)
            db.flush()
            self.document_id = doc.id

            texts = [
                ("Item 1A. Risk Factors", 12,
                 "Export controls on advanced computing products could restrict "
                 "our ability to sell into certain regions."),
                ("Item 7. MD&A", 44,
                 "Capital expenditure increased to expand data centre capacity "
                 "supporting Blackwell architecture workloads."),
                ("Item 7. MD&A", 45,
                 "Gaming revenue declined year over year as channel inventory "
                 "normalised."),
            ]
            replace_chunks(db, document_id=doc.id, chunks=[
                dict(chunk_hash=chunk_hash(company_id="0001045810",
                                           document_accession="0001045810-25-000023",
                                           page_number=page, text=t),
                     paragraph_ids=[f"{page}_{i+1}"], ordinal=i,
                     page_number=page, section_title=section,
                     section_path=["Part II", section], text=t,
                     char_count=len(t), token_estimate=max(1, len(t) // 4))
                for i, (section, page, t) in enumerate(texts)])

    # ---------------------------------------------------------------- upserts
    def test_unchanged_bytes_are_reported_as_not_new(self):
        """The signal the ingest worker uses to skip re-parsing."""
        from datetime import datetime, timezone
        from evident_db import session_scope
        from evident_db.repositories import upsert_document

        with session_scope(DSN) as db:
            _, is_new = upsert_document(
                db, company_id=self.company_id, accession="0001045810-25-000023",
                form_type="10-K", filed_at=date(2025, 2, 26),
                published_at=datetime(2025, 2, 26, 21, 5, tzinfo=timezone.utc),
                source_url="https://example.invalid/f.htm", source_format="html",
                content_sha256="a" * 64)
        self.assertFalse(is_new, "identical bytes should not be re-parsed")

    def test_changed_bytes_are_reported_as_new(self):
        from datetime import datetime, timezone
        from evident_db import session_scope
        from evident_db.repositories import upsert_document

        with session_scope(DSN) as db:
            _, is_new = upsert_document(
                db, company_id=self.company_id, accession="0001045810-25-000023",
                form_type="10-K/A", filed_at=date(2025, 3, 1),
                published_at=datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc),
                source_url="https://example.invalid/f.htm", source_format="html",
                content_sha256="b" * 64)
        self.assertTrue(is_new, "an amended filing must be re-parsed")

    def test_reingesting_a_company_updates_rather_than_duplicating(self):
        from sqlalchemy import func, select
        from evident_db import Company, session_scope
        from evident_db.repositories import upsert_company

        with session_scope(DSN) as db:
            upsert_company(db, cik="1045810", name="NVIDIA Corporation",
                           ticker="NVDA")
            db.flush()
            n = db.execute(select(func.count()).select_from(Company)
                           .where(Company.cik == "0001045810")).scalar_one()
            name = db.execute(select(Company.name)
                              .where(Company.cik == "0001045810")).scalar_one()
        self.assertEqual(n, 1)
        self.assertEqual(name, "NVIDIA Corporation")

    def test_topic_upsert_never_duplicates_and_widens_its_span(self):
        """The requirement, verified at the database rather than in code."""
        from sqlalchemy import func, select
        from evident_db import Topic, session_scope
        from evident_db.repositories import upsert_topic

        with session_scope(DSN) as db:
            upsert_topic(db, company_id=self.company_id, slug="blackwell",
                         label="Blackwell", observed_at=date(2024, 5, 1))
            upsert_topic(db, company_id=self.company_id, slug="blackwell",
                         label="Blackwell architecture", observed_at=date(2023, 2, 1))
            upsert_topic(db, company_id=self.company_id, slug="blackwell",
                         label="Blackwell", observed_at=date(2025, 2, 26))
            db.flush()
            rows = list(db.execute(select(Topic).where(
                Topic.company_id == self.company_id, Topic.slug == "blackwell")).scalars())

        self.assertEqual(len(rows), 1, "three ingests created more than one topic")
        self.assertEqual(rows[0].first_seen_at, date(2023, 2, 1),
                         "first_seen_at must move earlier, even out of order")
        self.assertEqual(rows[0].last_seen_at, date(2025, 2, 26))

    def test_mention_count_does_not_inflate_on_rerun(self):
        from sqlalchemy import select
        from evident_db import Chunk, Topic, session_scope
        from evident_db.repositories import add_topic_mention, upsert_topic

        with session_scope(DSN) as db:
            topic = upsert_topic(db, company_id=self.company_id, slug="capex",
                                 label="CapEx", observed_at=date(2025, 2, 26))
            db.flush()
            chunk = db.execute(select(Chunk).where(
                Chunk.document_id == self.document_id).limit(1)).scalar_one()
            first = add_topic_mention(db, topic_id=topic.id, chunk_id=chunk.id,
                                      document_id=self.document_id,
                                      observed_at=date(2025, 2, 26), quote="q")
            second = add_topic_mention(db, topic_id=topic.id, chunk_id=chunk.id,
                                       document_id=self.document_id,
                                       observed_at=date(2025, 2, 26), quote="q")
            db.flush()
            count = db.execute(select(Topic.mention_count)
                               .where(Topic.id == topic.id)).scalar_one()
        self.assertTrue(first)
        self.assertFalse(second, "re-running the builder re-counted a mention")
        self.assertEqual(count, 1)

    def test_dropped_risk_is_marked_not_deleted(self):
        from sqlalchemy import select
        from evident_db import Risk, session_scope
        from evident_db.repositories import mark_dropped_risks, upsert_risk

        with session_scope(DSN) as db:
            upsert_risk(db, company_id=self.company_id, slug="covid",
                        label="COVID-19 disruption", observed_at=date(2023, 2, 1))
            upsert_risk(db, company_id=self.company_id, slug="export",
                        label="Export controls", observed_at=date(2025, 2, 26))
            db.flush()
            mark_dropped_risks(db, company_id=self.company_id,
                               latest_filing_at=date(2025, 2, 26))
            db.flush()
            rows = {r.slug: r.status for r in db.execute(
                select(Risk).where(Risk.company_id == self.company_id)).scalars()}
        self.assertEqual(rows["covid"], "dropped")
        self.assertEqual(rows["export"], "active")

    # -------------------------------------------------------------- embedding
    def test_embedding_worker_writes_vectors_with_provenance(self):
        from sqlalchemy import select
        from evident_db import Chunk, session_scope
        from workers import embedding_worker

        result = embedding_worker.run(url=DSN, batch_size=2)
        self.assertGreaterEqual(result.embedded, 3)

        with session_scope(DSN) as db:
            rows = list(db.execute(select(Chunk).where(
                Chunk.document_id == self.document_id)).scalars())
        self.assertTrue(all(r.embedding is not None for r in rows))
        self.assertTrue(all(len(r.embedding) == 1536 for r in rows))
        self.assertTrue(all(r.embedding_provider and r.embedding_model
                            for r in rows), "vectors stored without provenance")

    def test_worker_refuses_a_dimension_mismatch(self):
        from evident_retrieval.embed import HashingEmbedder
        from workers import embedding_worker
        with self.assertRaises(ValueError) as ctx:
            embedding_worker.run(url=DSN, embedder=HashingEmbedder(dim=768))
        self.assertIn("Add a migration", str(ctx.exception))

    # -------------------------------------------------------------------- api
    async def _client(self):
        import httpx
        os.environ["DATABASE_URL"] = DSN
        import api.deps
        api.deps._factory = None                       # rebind to the test DSN
        from api.main import app
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t")

    async def test_company_memory_endpoint(self):
        async with await self._client() as c:
            r = await c.get("/v1/companies/nvda/memory")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["ticker"], "NVDA")
        self.assertEqual(body["document_count"], 1)
        self.assertIn("topics", body["counts"])

    async def test_unknown_company_is_404_not_empty_200(self):
        async with await self._client() as c:
            r = await c.get("/v1/companies/zzzz/memory")
        self.assertEqual(r.status_code, 404)
        self.assertIn("ZZZZ", r.json()["detail"])

    async def test_topic_endpoint_returns_mentions_with_citations(self):
        from evident_db import session_scope
        from evident_db.repositories import add_topic_mention, upsert_topic
        from sqlalchemy import select
        from evident_db import Chunk

        with session_scope(DSN) as db:
            topic = upsert_topic(db, company_id=self.company_id, slug="export-controls",
                                 label="Export controls", observed_at=date(2025, 2, 26))
            db.flush()
            chunk = db.execute(select(Chunk).where(
                Chunk.document_id == self.document_id,
                Chunk.page_number == 12)).scalar_one()
            add_topic_mention(db, topic_id=topic.id, chunk_id=chunk.id,
                              document_id=self.document_id,
                              observed_at=date(2025, 2, 26),
                              quote="Export controls on advanced computing products")

        async with await self._client() as c:
            r = await c.get("/v1/companies/nvda/topics/export-controls")
        self.assertEqual(r.status_code, 200)
        mention = r.json()["mentions"][0]
        self.assertEqual(mention["form_type"], "10-K")
        prov = mention["provenance"]
        self.assertEqual(prov["page"], 12)
        self.assertTrue(prov["paragraph_id"], "mention returned without a paragraph")
        self.assertTrue(prov["chunk_hash"], "mention returned without a chunk hash")
        self.assertIsNotNone(prov["document_id"])

    async def test_timeline_endpoint(self):
        from evident_db import session_scope
        from evident_db.repositories import add_timeline_event

        with session_scope(DSN) as db:
            add_timeline_event(db, company_id=self.company_id, kind="filing",
                               headline="10-K filed", occurred_at=date(2025, 2, 26),
                               ref=f"document:{self.document_id}",
                               document_id=self.document_id)
        async with await self._client() as c:
            r = await c.get("/v1/companies/nvda/timeline")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(e["kind"] == "filing" for e in r.json()))

    async def test_semantic_search_returns_cited_hits(self):
        from workers import embedding_worker
        embedding_worker.run(url=DSN)

        async with await self._client() as c:
            r = await c.post("/v1/search",
                             json={"query": "data centre capacity expansion",
                                   "ticker": "NVDA", "k": 3})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["hits"], "vector search returned nothing")
        top = body["hits"][0]
        self.assertIn("p.", top["citation"])
        # a hit must name the paragraphs it came from, not just the chunk
        self.assertTrue(top["chunk_hash"])
        self.assertTrue(top["paragraph_ids"], "search hit carried no paragraph ids")
        self.assertLessEqual(body["hits"][0]["score"], 1.0)
        # ordering must be by similarity
        scores = [h["score"] for h in body["hits"]]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
