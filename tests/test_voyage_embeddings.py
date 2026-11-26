"""Voyage embeddings: the provider, the backfill, and search reading what it wrote.

The Voyage API is faked: a client with Voyage's call shape that records every
request and raises the SDK's own error types on cue. No credentials are used.
What is worth asserting offline is what is sent (input type, width, dtype, no
truncation), what is retried and what is not, that a run which stops keeps
what it did and the next run finishes the rest, and that search embeds its
query with the same model the chunks were embedded with. Database tests skip
unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from unittest import mock

import voyageai.error as verr

from parallx_retrieval.embed import (EMBEDDING_DIM, EmbeddingAuthError, EmbeddingError,
                                     EmbeddingInvalidInput, EmbeddingInvalidOutput,
                                     EmbeddingRateLimited, HashingEmbedder, VoyageEmbedder,
                                     _Limiter, default_provider, price_per_mtok)

DSN = os.environ.get("TEST_DATABASE_URL")
_hash = HashingEmbedder()


class FakeVoyage:
    """Voyage's `Client.embed`, with vectors that carry some meaning (shared
    words), token counts, and failures scripted per call or per text."""

    def __init__(self, *, fail=(), poison=None, dim=EMBEDDING_DIM):
        self.calls: list[dict] = []
        self.fail = list(fail)       # exceptions to raise, one per call, in order
        self.poison = poison         # a text the API rejects
        self.dim = dim

    def embed(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail.pop(0)
        if self.poison and any(self.poison in t for t in kw["texts"]):
            raise verr.InvalidRequestError("input too long", http_status=400)
        vectors = _hash.embed(kw["texts"]) if self.dim == EMBEDDING_DIM else \
            [[0.1] * self.dim for _ in kw["texts"]]
        return type("R", (), {"embeddings": vectors,
                              "total_tokens": sum(len(t.split()) for t in kw["texts"])})()


def rate_limited(after=None):
    return verr.RateLimitError("slow down", http_status=429,
                               headers={"Retry-After": str(after)} if after else {})


def voyage(client, **kw):
    sleeps: list[float] = []
    e = VoyageEmbedder("voyage-4-large", client=client, api_key="test-key",
                       sleep=sleeps.append, **kw)
    return e, sleeps


# ------------------------------------------------------------ the provider


class Provider(unittest.TestCase):
    def test_configured_by_environment_as_voyage_4_large_at_1024_floats(self):
        with mock.patch.dict(os.environ, {"EMBEDDING_PROVIDER": "voyage",
                                          "EMBEDDING_MODEL": "voyage-4-large",
                                          "VOYAGE_API_KEY": "test-key"}):
            e = default_provider()
        self.assertEqual((e.name, e.model, e.dim), ("voyage", "voyage-4-large", 1024))
        c = FakeVoyage()
        voyage(c)[0].embed(["revenue grew"])
        call = c.calls[0]
        self.assertEqual((call["model"], call["input_type"], call["output_dimension"],
                          call["output_dtype"], call["truncation"]),
                         ("voyage-4-large", "document", 1024, "float", False))

    def test_queries_and_documents_differ_only_in_input_type(self):
        c = FakeVoyage()
        e, _ = voyage(c)
        e.embed(["a"])
        e.embed_query(["a"])
        doc, query = c.calls
        self.assertEqual((doc["input_type"], query["input_type"]), ("document", "query"))
        self.assertEqual({k: v for k, v in doc.items() if k != "input_type"},
                         {k: v for k, v in query.items() if k != "input_type"})

    def test_a_fixed_width_model_is_sent_neither_dimension_nor_dtype(self):
        c = FakeVoyage()
        VoyageEmbedder("voyage-finance-2", client=c, api_key="k").embed(["a"])
        self.assertNotIn("output_dimension", c.calls[0])
        self.assertNotIn("output_dtype", c.calls[0])

    def test_refused_credentials_are_not_retried(self):
        c = FakeVoyage(fail=[verr.AuthenticationError("bad key", http_status=401)])
        e, sleeps = voyage(c)
        with self.assertRaises(EmbeddingAuthError):
            e.embed(["a"])
        self.assertEqual((len(c.calls), sleeps), (1, []))

    def test_rate_limits_and_server_errors_are_retried_with_backoff(self):
        c = FakeVoyage(fail=[rate_limited(), verr.ServiceUnavailableError("busy",
                                                                           http_status=503)])
        e, sleeps = voyage(c)
        self.assertEqual(len(e.embed(["a b c"])), 1)
        self.assertEqual(len(c.calls), 3)
        self.assertTrue(1 <= sleeps[0] <= 1.25 and 2 <= sleeps[1] <= 2.5, sleeps)
        self.assertEqual((e.retries, e.requests, e.tokens), (2, 1, 3))

    def test_retry_after_is_honoured(self):
        e, sleeps = voyage(FakeVoyage(fail=[rate_limited(after=7)]))
        e.embed(["a"])
        self.assertEqual(sleeps, [7.0])

    def test_a_limit_that_does_not_lift_is_said_so(self):
        e, sleeps = voyage(FakeVoyage(fail=[rate_limited()] * 10))
        with self.assertRaises(EmbeddingRateLimited) as ctx:
            e.embed(["a"])
        self.assertIn("after 7 attempts", str(ctx.exception))
        self.assertEqual(len(sleeps), 6)

    def test_a_rejected_request_is_not_retried(self):
        c = FakeVoyage(poison="X")
        e, sleeps = voyage(c)
        with self.assertRaises(EmbeddingInvalidInput):
            e.embed(["fine", "X" * 10])
        self.assertEqual((len(c.calls), sleeps), (1, []))

    def test_a_query_waits_seconds_not_minutes(self):
        e, sleeps = voyage(FakeVoyage(fail=[rate_limited(after=30)]))
        with self.assertRaises(EmbeddingRateLimited):
            e.embed_query(["what changed"])
        self.assertEqual(sleeps, [], "someone is waiting: no 30-second sleep")

    def test_unusable_vectors_never_leave_the_provider(self):
        class Bad(FakeVoyage):
            def __init__(self, v):
                super().__init__()
                self.v = v

            def embed(self, **kw):
                return type("R", (), {"embeddings": [self.v], "total_tokens": 1})()
        for v in ([float("nan")] + [0.1] * 1023, [0.0] * 1024):
            with self.assertRaises(EmbeddingInvalidOutput):
                voyage(Bad(v))[0].embed(["a"])
        with self.assertRaises(EmbeddingError) as ctx:
            voyage(FakeVoyage(dim=512))[0].embed(["a"])
        self.assertIn("512", str(ctx.exception))

    def test_requests_are_paced_under_a_per_minute_budget(self):
        now = [0.0]
        waits = []

        def sleep(s):
            waits.append(s)
            now[0] += s
        lim = _Limiter(rpm=2, tpm=1000, clock=lambda: now[0], sleep=sleep)
        lim.acquire(100)
        lim.acquire(100)
        lim.acquire(100)                         # third request in the minute
        self.assertEqual(waits, [60.0])
        lim.acquire(950)                         # over the token budget: waits again
        self.assertEqual(len(waits), 2)

    def test_the_limiter_counts_what_the_provider_reported(self):
        now = [0.0]
        lim = _Limiter(rpm=None, tpm=1000, clock=lambda: now[0], sleep=lambda s: None)
        lim.acquire(900)          # estimated
        lim.settle(600)           # what the API counted
        self.assertEqual(sum(t for _, t in lim.sent), 600)

    def test_a_request_never_carries_more_than_the_minute_allows(self):
        e, _ = voyage(FakeVoyage(), tpm=10_000)
        self.assertEqual(e.request_budget, 10_000)
        self.assertEqual(voyage(FakeVoyage())[0].request_budget, 120_000)

    def test_cost_is_estimated_from_list_price(self):
        self.assertEqual(price_per_mtok("voyage-4-large"), 0.12)
        self.assertIsNone(price_per_mtok("voyage-unknown"))
        with mock.patch.dict(os.environ, {"EMBEDDING_PRICE_PER_MTOK": "0.2"}):
            self.assertEqual(price_per_mtok("voyage-unknown"), 0.2)


# ------------------------------------------------------------ the backfill


def corpus(texts_by_ticker: dict[str, list[str]]):
    """Companies, a filing each, and a chunk per text."""
    from parallx_db import Chunk, Company, Document, session_scope
    ids = {}
    with session_scope(DSN) as db:
        for n, (ticker, texts) in enumerate(texts_by_ticker.items()):
            co = Company(cik=f"{n + 1:010d}", ticker=ticker, name=f"{ticker} Inc")
            db.add(co)
            db.flush()
            doc = Document(company_id=co.id, accession=f"0000000000-26-{n:06d}",
                           form_type="10-K", filed_at=date(2026, 2, 1),
                           published_at=datetime(2026, 2, 1, 21, tzinfo=timezone.utc),
                           source_url="https://example.test", source_format="html",
                           content_sha256="0" * 64)
            db.add(doc)
            db.flush()
            for i, t in enumerate(texts):
                c = Chunk(document_id=doc.id, chunk_hash=f"{ticker}-{i}", ordinal=i,
                          paragraph_ids=[f"p{i}"], text=t, char_count=len(t),
                          token_estimate=max(1, len(t) // 4), page_number=1)
                db.add(c)
                db.flush()
                ids[(ticker, i)] = c.id
    return ids


def stored(provider="voyage", model="voyage-4-large"):
    from sqlalchemy import text
    from parallx_db import session_scope
    with session_scope(DSN) as db:
        return db.execute(text(
            "select id, vector_dims(embedding) from chunks where embedding is not null "
            "and embedding_provider = :p and embedding_model = :m order by id"),
            {"p": provider, "m": model}).all()


def chunk_count() -> int:
    from sqlalchemy import text
    from parallx_db import session_scope
    with session_scope(DSN) as db:
        return db.execute(text("select count(*) from chunks")).scalar_one()


RISKS = [f"risk factor {i}: export controls and supply chain constraints" for i in range(7)]


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Backfill(unittest.TestCase):
    def setUp(self):
        from test_evidence_e2e import _reset_schema
        _reset_schema()

    def run_with(self, client, **kw):
        from workers import embedding_worker
        return embedding_worker.run(url=DSN, embedder=voyage(client)[0], **kw)

    def test_batches_of_the_size_asked_and_empty_chunks_never_sent(self):
        corpus({"NVDA": RISKS + ["", "   "]})
        c = FakeVoyage()
        r = self.run_with(c, batch_size=3)
        self.assertEqual((r.eligible, r.empty, r.already, r.remaining, r.embedded),
                         (7, 2, 0, 7, 7))
        self.assertEqual([len(call["texts"]) for call in c.calls], [3, 3, 1])
        self.assertTrue(all(t.strip() for call in c.calls for t in call["texts"]))
        self.assertEqual((r.tokens, r.tokens_reported), (sum(len(t.split()) for t in RISKS),
                                                          True))
        self.assertEqual([d for _, d in stored()], [1024] * 7)

    def test_a_run_that_stops_keeps_its_work_and_the_next_finishes_it(self):
        from workers.embedding_worker import EmbeddingStopped
        corpus({"NVDA": RISKS})
        # two batches go through, then the key is revoked
        c = FakeVoyage()
        real = c.embed

        def revoked_after_two(**kw):
            if len(c.calls) >= 2:
                c.calls.append(kw)
                raise verr.AuthenticationError("revoked", http_status=401)
            return real(**kw)
        c.embed = revoked_after_two
        with self.assertRaises(EmbeddingStopped) as ctx:
            self.run_with(c, batch_size=2)
        done = ctx.exception.result
        self.assertEqual((done.embedded, done.left), (4, 3))
        self.assertIn("credentials refused", done.stopped)
        self.assertEqual(len(stored()), 4, "committed as it went")

        again = FakeVoyage()
        r = self.run_with(again, batch_size=2)
        self.assertEqual((r.already, r.remaining, r.embedded), (4, 3, 3))
        self.assertEqual(sum(len(call["texts"]) for call in again.calls), 3,
                         "only what was left")
        self.assertEqual(len(stored()), 7)

    def test_a_rejected_chunk_is_isolated_and_the_rest_embedded(self):
        ids = corpus({"NVDA": RISKS[:3] + ["POISON " * 5] + RISKS[3:]})
        c = FakeVoyage(poison="POISON")
        r = self.run_with(c, batch_size=8)
        self.assertEqual((r.embedded, r.failed, r.failed_ids), (7, 1, [ids[("NVDA", 3)]]))
        self.assertEqual(len(stored()), 7)
        self.assertNotIn(ids[("NVDA", 3)], {i for i, _ in stored()})

    def test_a_rate_limit_shrinks_the_batches_and_carries_on(self):
        corpus({"NVDA": RISKS})
        # the first two requests (7 chunks, then 3) are too big for the minute
        c = FakeVoyage(fail=[rate_limited()] * 7 + [rate_limited()] * 7)
        r = self.run_with(c, batch_size=7)
        self.assertEqual((r.embedded, r.failed), (7, 0))
        self.assertGreater(len(r.errors), 0)

    def test_nothing_is_embedded_twice_and_no_row_is_added(self):
        corpus({"NVDA": RISKS})
        before = chunk_count()
        self.run_with(FakeVoyage())
        again = FakeVoyage()
        r = self.run_with(again)
        self.assertEqual((r.already, r.remaining, again.calls), (7, 0, []))
        self.assertEqual(chunk_count(), before)

    def test_another_models_vectors_are_replaced_and_force_redoes_this_ones(self):
        from workers import embedding_worker
        corpus({"NVDA": RISKS})
        embedding_worker.run(url=DSN, embedder=HashingEmbedder())
        self.assertEqual(len(stored("local", "hashing-v1")), 7)
        r = self.run_with(FakeVoyage())
        self.assertEqual((r.already, r.embedded), (0, 7))
        self.assertEqual(stored("local", "hashing-v1"), [], "replaced, not kept beside")
        c = FakeVoyage()
        forced = self.run_with(c, force=True)
        self.assertEqual((forced.already, forced.embedded), (7, 7))
        self.assertEqual(chunk_count(), 7)

    def test_batches_fit_the_accounts_token_budget(self):
        from workers import embedding_worker
        corpus({"NVDA": ["word " * 400] * 6})        # ~500 estimated tokens each
        c = FakeVoyage()
        now = [0.0]

        def sleep(s):
            now[0] += s
        embedder = VoyageEmbedder("voyage-4-large", client=c, api_key="k", tpm=2000,
                                  sleep=sleep, clock=lambda: now[0])
        embedding_worker.run(url=DSN, embedder=embedder, batch_size=128)
        # 2,000 a minute, packed to 60%: two chunks a request, paced a minute apart
        self.assertEqual([len(call["texts"]) for call in c.calls], [2, 2, 2])
        self.assertGreaterEqual(now[0], 60.0)

    def test_a_ticker_scopes_the_run_and_a_dry_run_sends_nothing(self):
        corpus({"NVDA": RISKS, "MSFT": ["azure capacity", "ai infrastructure capex"]})
        c = FakeVoyage()
        dry = self.run_with(c, ticker="msft", dry_run=True)
        self.assertEqual((dry.eligible, dry.remaining, dry.embedded, c.calls),
                         (2, 2, 0, []))
        r = self.run_with(c, ticker="MSFT", max_chunks=1)
        self.assertEqual((r.remaining, r.planned, r.embedded, r.left), (2, 1, 1, 1))
        self.assertEqual(len(stored()), 1)

    def test_a_width_other_than_the_columns_is_refused_before_any_call(self):
        from workers import embedding_worker
        corpus({"NVDA": RISKS})
        c = FakeVoyage()
        with self.assertRaises(ValueError):
            embedding_worker.run(url=DSN, embedder=VoyageEmbedder(
                "voyage-4-large", dim=2048, client=c, api_key="k"))
        self.assertEqual(c.calls, [])


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Command(unittest.TestCase):
    def setUp(self):
        from test_evidence_e2e import _reset_schema
        _reset_schema()
        corpus({"NVDA": RISKS})
        self.client = FakeVoyage()
        env = {"DATABASE_URL": DSN, "EMBEDDING_PROVIDER": "voyage",
               "EMBEDDING_MODEL": "voyage-4-large", "VOYAGE_API_KEY": "test-key"}
        for p in (mock.patch.dict(os.environ, env),
                  mock.patch("parallx_retrieval.embed.get_provider",
                             lambda name, model=None: voyage(self.client)[0]),
                  mock.patch("tools.generate_embeddings.load_env", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def main(self, *argv):
        from tools import generate_embeddings
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = generate_embeddings.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_then_a_sample_then_the_rest(self):
        code, out, _ = self.main("--provider", "voyage", "--dry-run")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"Total eligible chunks\s+7")
        self.assertRegex(out, r"Remaining\s+7")
        self.assertEqual(self.client.calls, [])

        code, out, _ = self.main("--provider", "voyage", "--limit", "5", "--batch-size", "2")
        self.assertEqual(code, 1, "two left")
        self.assertRegex(out, r"Embedded now\s+5 in 3 requests")
        self.assertRegex(out, r"Tokens processed\s+\d+")
        self.assertRegex(out, r"Cost\s+\$0\.\d{4} \(list price, estimated\)")
        self.assertIn("Verified in PostgreSQL: 5 chunks hold voyage/voyage-4-large vectors, "
                      "1024–1024 dimensions", out)

        code, out, _ = self.main("--provider", "voyage")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"Already embedded\s+5")
        self.assertIn("Verified in PostgreSQL: 7 chunks", out)

    def test_the_hashing_provider_is_refused(self):
        code, _, err = self.main("--provider", "hashing")
        self.assertEqual(code, 2)
        self.assertIn("for tests", err)


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Search(unittest.IsolatedAsyncioTestCase):
    """POST /v1/search reads the vectors the backfill wrote, with a query
    embedded by the same model — and nothing another model embedded."""

    async def test_queries_are_embedded_like_the_documents_and_only_they_are_searched(self):
        from test_evidence_e2e import _ApiMixin, _reset_schema
        from workers import embedding_worker
        _reset_schema()
        ids = corpus({"NVDA": RISKS + ["data center demand for accelerated computing"],
                      "MSFT": ["azure ai infrastructure spending rose"]})
        c = FakeVoyage()
        embedder = voyage(c)[0]
        embedding_worker.run(url=DSN, embedder=embedder, ticker="NVDA")
        # MSFT's chunk embedded by another model: never a hit for a Voyage query
        embedding_worker.run(url=DSN, embedder=HashingEmbedder(), ticker="MSFT")
        api = _ApiMixin()
        with mock.patch("parallx_retrieval.embed.default_provider", lambda: embedder), \
                mock.patch.dict(os.environ, {"SEC_USER_AGENT": ""}):
            async with await api._client() as client:
                r = await client.post("/v1/search", json={"query": "data center demand"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        query_call = c.calls[-1]
        self.assertEqual((query_call["input_type"], query_call["model"],
                          query_call["output_dimension"]), ("query", "voyage-4-large", 1024))
        self.assertTrue(body["hits"])
        hit_ids = {h["chunk_id"] for h in body["hits"]}
        self.assertNotIn(ids[("MSFT", 0)], hit_ids, "another model's vector")
        self.assertEqual(body["hits"][0]["chunk_id"], ids[("NVDA", 7)])
        self.assertTrue(all(h["accession"] and h["citation"] and h["paragraph_ids"]
                            for h in body["hits"]))


    async def test_a_query_model_of_another_width_is_refused_not_compared(self):
        from test_evidence_e2e import _ApiMixin, _reset_schema
        _reset_schema()
        corpus({"NVDA": RISKS})
        c = FakeVoyage(dim=2048)
        wide = VoyageEmbedder("voyage-4-large", dim=2048, client=c, api_key="k")
        with mock.patch("parallx_retrieval.embed.default_provider", lambda: wide), \
                mock.patch.dict(os.environ, {"SEC_USER_AGENT": ""}):
            async with await _ApiMixin()._client() as client:
                r = await client.post("/v1/search", json={"query": "export controls"})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertIn("2048", r.json()["message"])
        self.assertEqual(c.calls, [], "refused before any API call")


if __name__ == "__main__":
    unittest.main()
