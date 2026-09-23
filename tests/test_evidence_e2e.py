"""Evidence resolution, against a real ingested filing in real Postgres.

The fixture is the synthetic 10-K, deliberately: it has both of the cases most
likely to resolve wrong. Fifty-three of its chunks span a page break, so a
citation resolved to the chunk's stored page lands on the wrong one; and 56 are
tables, which have no paragraph ids and must be anchored by chunk instead.

The invariant everything here protects: **a citation can always be found on
screen.** Every anchor a citation resolves to must exist in the reading view,
on the page the citation says.

Skipped unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import json
import os
import unittest

from test_ingest_e2e import DSN, fixture_origin

TICKER = "NVDA"


def _reset_schema():
    from sqlalchemy import text
    from evident_db import Base, make_engine
    engine = make_engine(DSN)
    Base.metadata.drop_all(engine)
    with engine.begin() as c:
        c.execute(text("create extension if not exists vector"))
    Base.metadata.create_all(engine)


class _ApiMixin:
    async def _client(self):
        import httpx
        os.environ["DATABASE_URL"] = DSN
        import api.deps
        api.deps._factory = None                     # rebind to the test DSN
        from api.main import app
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t")


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Resolution(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    """Read-only against one ingest, so the filing is ingested once."""

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import select
        from evident_db import Chunk, Document, session_scope
        from evident_parser.anchors import paragraph_page
        from workers.ingest_worker import ingest_ticker

        _reset_schema()
        with fixture_origin():
            ingest_ticker(TICKER, limit=1, url=DSN)

        with session_scope(DSN) as db:
            doc = db.execute(select(Document)).scalar_one()
            chunks = list(db.execute(select(Chunk).where(
                Chunk.document_id == doc.id).order_by(Chunk.ordinal)).scalars())
            cls.document_id = doc.id
            cls.chunks = [(c.id, list(c.paragraph_ids or []), c.page_number,
                           c.chunk_hash) for c in chunks]

        # A prose chunk whose last paragraph is on a later page than the chunk
        # starts on: the case a chunk-level page gets wrong.
        cls.spanning = next(
            (cid, pids, page) for cid, pids, page, _ in cls.chunks
            if pids and paragraph_page(pids[-1], page) != page)
        cls.table = next(cid for cid, pids, _, _ in cls.chunks if not pids)

    # ----------------------------------------------------------- one citation
    async def test_citation_resolves_the_paragraphs_page_not_the_chunks(self):
        """The failure this whole module is shaped around."""
        from evident_parser.anchors import paragraph_page

        chunk_id, pids, chunk_page = self.spanning
        last = pids[-1]
        async with await self._client() as c:
            r = await c.get(f"/v1/evidence/{chunk_id}", params={"paragraph_id": last})
        self.assertEqual(r.status_code, 200, r.text)
        ev = r.json()

        self.assertEqual(ev["page"], paragraph_page(last))
        self.assertNotEqual(ev["page"], chunk_page,
                            "resolved to the chunk's first page — the bug")
        self.assertEqual(ev["paragraph_id"], last)
        self.assertEqual(ev["anchors"], [f"p-{last}"])
        self.assertIn(f"p. {ev['page']}", ev["citation"])

    async def test_citing_a_paragraph_returns_that_paragraph_not_the_chunk(self):
        chunk_id, pids, _ = self.spanning
        async with await self._client() as c:
            whole = (await c.get(f"/v1/evidence/{chunk_id}")).json()
            one = (await c.get(f"/v1/evidence/{chunk_id}",
                               params={"paragraph_id": pids[-1]})).json()
        self.assertLess(len(one["text"]), len(whole["text"]))
        self.assertIn(one["text"], whole["text"])

    async def test_naming_only_a_chunk_highlights_all_of_it(self):
        chunk_id, pids, chunk_page = self.spanning
        async with await self._client() as c:
            ev = (await c.get(f"/v1/evidence/{chunk_id}")).json()
        self.assertEqual(ev["anchors"], [f"p-{p}" for p in pids])
        self.assertEqual(ev["paragraph_id"], pids[0], "scroll target is the first")
        self.assertEqual(ev["page"], chunk_page)

    async def test_a_table_is_anchored_by_its_chunk(self):
        async with await self._client() as c:
            ev = (await c.get(f"/v1/evidence/{self.table}")).json()
        self.assertEqual(ev["anchors"], [f"c-{self.table}"])
        self.assertIsNone(ev["paragraph_id"])
        self.assertEqual(ev["paragraph_ids"], [])

    async def test_a_table_cited_by_its_hash_resolves_to_the_whole_table(self):
        # extraction cites a table chunk by its hash, since it has no paragraphs
        chunk_hash = next(h for cid, _, _, h in self.chunks if cid == self.table)
        async with await self._client() as c:
            r = await c.get(f"/v1/evidence/{self.table}",
                            params={"paragraph_id": chunk_hash})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["anchors"], [f"c-{self.table}"])

    async def test_bounding_box_is_null_not_invented(self):
        chunk_id, _, _ = self.spanning
        async with await self._client() as c:
            ev = (await c.get(f"/v1/evidence/{chunk_id}")).json()
        self.assertIn("bounding_box", ev)
        self.assertIsNone(ev["bounding_box"])
        self.assertEqual(ev["source_format"], "html")

    async def test_a_paragraph_from_another_chunk_is_refused(self):
        chunk_id, _, _ = self.spanning
        foreign = next(pids[0] for cid, pids, _, _ in self.chunks
                       if pids and cid != chunk_id and pids[0] not in self.spanning[1])
        async with await self._client() as c:
            r = await c.get(f"/v1/evidence/{chunk_id}", params={"paragraph_id": foreign})
        self.assertEqual(r.status_code, 404)
        self.assertIn("not part of chunk", r.json()["detail"])

    async def test_an_unknown_chunk_is_404(self):
        async with await self._client() as c:
            r = await c.get("/v1/evidence/99999999")
        self.assertEqual(r.status_code, 404)

    # ------------------------------------------------------- many citations
    async def test_multiple_citations_resolve_in_order(self):
        (a, a_pids, _), b = self.spanning, self.table
        body = {"citations": [
            {"chunk_id": a, "paragraph_id": a_pids[-1]},
            {"chunk_id": b},
            {"chunk_id": a},
        ]}
        async with await self._client() as c:
            r = await c.post("/v1/evidence/resolve", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()["citations"]
        self.assertEqual([x["index"] for x in out], [0, 1, 2])
        self.assertTrue(all(x["resolved"] for x in out))
        self.assertEqual(out[0]["evidence"]["anchors"], [f"p-{a_pids[-1]}"])
        self.assertEqual(out[1]["evidence"]["anchors"], [f"c-{b}"])
        self.assertEqual(len(out[2]["evidence"]["anchors"]), len(a_pids))

    async def test_one_bad_citation_does_not_fail_the_answer(self):
        """Four good citations should survive one stale one."""
        chunk_id, pids, _ = self.spanning
        body = {"citations": [
            {"chunk_id": chunk_id},
            {"chunk_id": 99999999},
            {"chunk_id": chunk_id, "paragraph_id": "no_such_paragraph"},
            {"chunk_id": self.table},
        ]}
        async with await self._client() as c:
            r = await c.post("/v1/evidence/resolve", json=body)
        self.assertEqual(r.status_code, 200)
        out = r.json()["citations"]
        self.assertEqual([x["resolved"] for x in out], [True, False, False, True])
        self.assertIn("no chunk", out[1]["error"])
        self.assertIn("not part of chunk", out[2]["error"])
        self.assertIsNone(out[1]["evidence"])

    async def test_an_empty_answer_is_refused(self):
        async with await self._client() as c:
            r = await c.post("/v1/evidence/resolve", json={"citations": []})
        self.assertEqual(r.status_code, 422)

    # ----------------------------------------------------------- reading view
    async def test_every_paragraph_appears_exactly_once(self):
        """Overlapping chunks share paragraphs; the reading view must not."""
        async with await self._client() as c:
            view = (await c.get(f"/v1/documents/{self.document_id}/pages")).json()
        anchors = [b["anchor"] for p in view["pages"] for b in p["blocks"]]
        self.assertEqual(len(anchors), len(set(anchors)), "a paragraph rendered twice")

        expected = ({f"p-{pid}" for _, pids, _, _ in self.chunks for pid in pids}
                    | {f"c-{cid}" for cid, pids, _, _ in self.chunks if not pids})
        self.assertEqual(set(anchors), expected, "a paragraph went missing")

    async def test_every_paragraph_sits_on_the_page_its_id_names(self):
        from evident_parser.anchors import paragraph_page
        async with await self._client() as c:
            view = (await c.get(f"/v1/documents/{self.document_id}/pages")).json()
        pages = [p["page"] for p in view["pages"]]
        self.assertEqual(pages, sorted(pages, key=lambda x: (x is None, x or 0)))
        for page in view["pages"]:
            for block in page["blocks"]:
                if block["paragraph_id"]:
                    self.assertEqual(page["page"], paragraph_page(block["paragraph_id"]))

    async def test_every_citation_can_be_found_on_screen(self):
        """The invariant: resolve every chunk, find every anchor, on its page."""
        async with await self._client() as c:
            view = (await c.get(f"/v1/documents/{self.document_id}/pages")).json()
            where = {b["anchor"]: p["page"] for p in view["pages"] for b in p["blocks"]}
            out = []
            ids = [cid for cid, _, _, _ in self.chunks]
            for i in range(0, len(ids), 100):          # the endpoint caps at 100
                body = {"citations": [{"chunk_id": cid} for cid in ids[i:i + 100]]}
                r = await c.post("/v1/evidence/resolve", json=body)
                self.assertEqual(r.status_code, 200, r.text)
                out += r.json()["citations"]
        self.assertEqual(len(out), len(ids))

        self.assertTrue(all(x["resolved"] for x in out))
        for x in out:
            ev = x["evidence"]
            for anchor in ev["anchors"]:
                self.assertIn(anchor, where, f"chunk {ev['chunk_id']}: {anchor} not rendered")
            self.assertEqual(where[ev["anchors"][0]], ev["page"],
                             f"chunk {ev['chunk_id']}: scrolls to the wrong page")

    async def test_tables_render_as_tables(self):
        async with await self._client() as c:
            view = (await c.get(f"/v1/documents/{self.document_id}/pages")).json()
        kinds = {b["anchor"]: b["kind"] for p in view["pages"] for b in p["blocks"]}
        self.assertEqual(kinds[f"c-{self.table}"], "table")

    async def test_an_unknown_document_is_404(self):
        async with await self._client() as c:
            r = await c.get("/v1/documents/99999999/pages")
        self.assertEqual(r.status_code, 404)


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Confidence(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    """Confidence belongs to a claim, so it is scoped to the claim's mentions."""

    def setUp(self):
        from datetime import date
        from sqlalchemy import select
        from evident_db import Chunk, Company, Document, session_scope
        from evident_db.repositories import add_entity_mention, upsert_entity
        from workers.ingest_worker import ingest_ticker

        _reset_schema()
        with fixture_origin():
            ingest_ticker(TICKER, limit=1, url=DSN)
        with session_scope(DSN) as db:
            company = db.execute(select(Company)).scalar_one()
            doc = db.execute(select(Document)).scalar_one()
            chunk = next(c for c in db.execute(select(Chunk).where(
                Chunk.document_id == doc.id).order_by(Chunk.ordinal)).scalars()
                if len(c.paragraph_ids or []) >= 2)
            self.chunk_id, self.p1, self.p2 = chunk.id, *chunk.paragraph_ids[:2]

            def mention(slug, pid, conf):
                e = upsert_entity(db, company_id=company.id, entity_type="risk",
                                  slug=slug, name=slug, observed_at=date(2025, 2, 26))
                db.flush()
                add_entity_mention(db, entity_id=e.id, document_id=doc.id,
                                   chunk_id=chunk.id, observed_at=date(2025, 2, 26),
                                   quote="q", paragraph_id=pid, confidence=conf)

            mention("export_controls", self.p1, 0.95)
            mention("supply_chain", self.p2, 0.60)

    async def test_confidence_is_the_cited_paragraphs(self):
        async with await self._client() as c:
            p1 = (await c.get(f"/v1/evidence/{self.chunk_id}",
                              params={"paragraph_id": self.p1})).json()
            p2 = (await c.get(f"/v1/evidence/{self.chunk_id}",
                              params={"paragraph_id": self.p2})).json()
        self.assertAlmostEqual(p1["confidence"], 0.95)
        self.assertAlmostEqual(p2["confidence"], 0.60,
                               msg="borrowed the neighbouring paragraph's confidence")

    async def test_naming_an_entity_scopes_confidence_to_it(self):
        async with await self._client() as c:
            ev = (await c.get(f"/v1/evidence/{self.chunk_id}",
                              params={"entity": "supply_chain"})).json()
        self.assertAlmostEqual(ev["confidence"], 0.60)

    async def test_confidence_is_null_when_nothing_was_extracted(self):
        async with await self._client() as c:
            ev = (await c.get(f"/v1/evidence/{self.chunk_id}",
                              params={"entity": "no_such_entity"})).json()
        self.assertIsNone(ev["confidence"])

    async def test_a_mention_carries_the_chunk_a_viewer_needs(self):
        async with await self._client() as c:
            body = (await c.get(f"/v1/companies/{TICKER.lower()}"
                                "/entities/export_controls")).json()
        prov = body["mentions"][0]["provenance"]
        self.assertEqual(prov["chunk_id"], self.chunk_id)
        self.assertEqual(prov["paragraph_id"], self.p1)


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class ExtractionCitesParagraphs(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    """Regression: mentions used to record the chunk's first paragraph.

    Extraction sent each chunk as one block labelled with its first paragraph
    id, so that was the only id the model could cite. An entity in paragraph
    four, two pages on, was recorded as paragraph one on the first page — and
    the viewer highlighted the wrong text.
    """

    def setUp(self):
        _reset_schema()
        from workers.ingest_worker import ingest_ticker
        with fixture_origin():
            ingest_ticker(TICKER, limit=1, url=DSN)

    async def test_a_mention_records_the_paragraph_that_supports_it(self):
        from sqlalchemy import select
        from evident_db import (Chunk, Company, Document, EntityMention,
                                session_scope)
        from evident_parser.anchors import paragraph_page
        from workers.memory_builder import build_for_document

        sent: list[list[str]] = []

        class _Client:
            """Cites the *last* paragraph it is shown."""
            @property
            def messages(self):
                class _M:
                    def create(self, **kw):
                        body = kw["messages"][0]["content"]
                        ids = [part.split("]", 1)[0].lstrip("[")
                               for part in body.split("\n\n")]
                        sent.append(ids)
                        text = body.split("\n\n")[-1].split("] ", 1)[1]
                        payload = {"entities": [{
                            "name": "Export Controls", "entity_type": "risk",
                            "confidence": 0.9, "paragraph_id": ids[-1],
                            "quote": text[:50]}]}
                        return type("R", (), {
                            "stop_reason": "end_turn",
                            "content": [type("T", (), {"type": "text",
                                                       "text": json.dumps(payload)})()]})()
                return _M()

        with session_scope(DSN) as db:
            company = db.execute(select(Company)).scalar_one()
            doc = db.execute(select(Document)).scalar_one()
            chunks = list(db.execute(select(Chunk).where(
                Chunk.document_id == doc.id).order_by(Chunk.ordinal)).scalars())
            # the first chunk whose paragraphs cross a page break
            target = next(c for c in chunks if c.paragraph_ids and
                          paragraph_page(c.paragraph_ids[-1], c.page_number)
                          != c.page_number)
            upto = chunks.index(target) + 1
            build_for_document(db, company_id=company.id, document=doc,
                               client=_Client(), limit=upto)
            target_ids, target_page = list(target.paragraph_ids), target.page_number

        # the model is now shown every paragraph, each under its own id
        self.assertIn(target_ids, sent)

        with session_scope(DSN) as db:
            m = db.execute(select(EntityMention).where(
                EntityMention.paragraph_id == target_ids[-1])).scalar_one()
        self.assertEqual(m.paragraph_id, target_ids[-1])
        self.assertEqual(m.page, paragraph_page(target_ids[-1]))
        self.assertNotEqual(m.page, target_page,
                            "recorded the chunk's first page — the old bug")

        # and resolving the mention's citation lands on that paragraph
        async with await self._client() as c:
            ev = (await c.get(f"/v1/evidence/{m.chunk_id}",
                              params={"paragraph_id": m.paragraph_id})).json()
        self.assertEqual(ev["anchors"], [f"p-{target_ids[-1]}"])
        self.assertEqual(ev["page"], m.page)


if __name__ == "__main__":
    unittest.main()
