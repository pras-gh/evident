"""Embedding providers: one interface, four implementations, one config change.

The API calls are faked. What is worth asserting offline is the shape of the
request each provider builds and what it refuses to return — a provider that
quietly hands back the wrong width, or embeds a query as a document, produces
an index that works exactly well enough to be trusted.
"""
from __future__ import annotations

import os
import unittest

from parallx_db import EMBEDDING_DIM as COLUMN_DIM
from parallx_retrieval.embed import (EMBEDDING_DIM, EmbeddingError,
                                     EmbeddingProvider, GeminiEmbedder,
                                     HashingEmbedder, OpenAIEmbedder, PROVIDERS,
                                     VoyageEmbedder, default_provider, get_provider)


def vec(n=EMBEDDING_DIM, fill=0.1):
    return [fill] * n


class SharedWidth(unittest.TestCase):
    def test_the_column_and_the_providers_agree(self):
        # if these ever diverge every write fails at the database, or worse,
        # succeeds into an index whose vectors mean different things
        self.assertEqual(EMBEDDING_DIM, COLUMN_DIM)

    def test_the_width_is_one_every_provider_can_emit(self):
        # voyage-finance-2 is 1024-only; that is what pins this
        self.assertEqual(EMBEDDING_DIM, 1024)

    def test_every_provider_defaults_to_it(self):
        for name in PROVIDERS:
            self.assertEqual(get_provider(name).dim, EMBEDDING_DIM, name)


class Interface(unittest.TestCase):
    def test_every_implementation_satisfies_the_interface(self):
        for name in PROVIDERS:
            p = get_provider(name)
            self.assertIsInstance(p, EmbeddingProvider, name)
            self.assertTrue(p.name and p.model, name)
            self.assertTrue(callable(p.embed) and callable(p.embed_query), name)

    def test_embed_returns_plain_vectors_not_a_wrapper(self):
        out = HashingEmbedder(dim=8).embed(["a", "b"])
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 2)
        self.assertIsInstance(out[0], list)
        self.assertIsInstance(out[0][0], float)

    def test_unknown_provider_names_itself_and_the_alternatives(self):
        with self.assertRaises(EmbeddingError) as ctx:
            get_provider("cohere")
        self.assertIn("cohere", str(ctx.exception))
        self.assertIn("voyage", str(ctx.exception))


class Configuration(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_DIM")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_switching_provider_is_one_variable(self):
        os.environ["EMBEDDING_PROVIDER"] = "voyage"
        self.assertEqual(default_provider().name, "voyage")
        os.environ["EMBEDDING_PROVIDER"] = "openai"
        self.assertEqual(default_provider().name, "openai")

    def test_model_can_be_overridden_without_touching_code(self):
        os.environ.update({"EMBEDDING_PROVIDER": "voyage",
                           "EMBEDDING_MODEL": "voyage-4-large"})
        self.assertEqual(default_provider().model, "voyage-4-large")

    def test_unconfigured_refuses_rather_than_falling_back_to_hashing(self):
        # the whole point of removing the default: hash vectors make retrieval
        # look like it works
        with self.assertRaises(EmbeddingError) as ctx:
            default_provider()
        self.assertIn("EMBEDDING_PROVIDER", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, HashingEmbedder)


class WidthEnforcement(unittest.TestCase):
    class _Wrong:
        """A client that returns the wrong width."""
        def embed(self, **kw):
            return type("R", (), {"embeddings": [vec(999)]})()

    def test_a_wrong_width_is_refused_before_it_reaches_the_database(self):
        p = VoyageEmbedder(client=self._Wrong(), api_key="k")
        with self.assertRaises(EmbeddingError) as ctx:
            p.embed(["one"])
        self.assertIn("999", str(ctx.exception))
        self.assertIn("1024", str(ctx.exception))

    def test_a_short_count_is_refused(self):
        class _Short:
            def embed(self, **kw):
                return type("R", (), {"embeddings": [vec()]})()
        with self.assertRaises(EmbeddingError) as ctx:
            VoyageEmbedder(client=_Short(), api_key="k").embed(["one", "two"])
        self.assertIn("got 1", str(ctx.exception))


class _FakeVoyage:
    def __init__(self, n=1):
        self.calls = []
        self.n = n

    def embed(self, **kw):
        self.calls.append(kw)
        return type("R", (), {"embeddings": [vec() for _ in kw["texts"]]})()


class Voyage(unittest.TestCase):
    def test_documents_and_queries_use_different_input_types(self):
        # Voyage retrieves measurably better when told which side it is
        c = _FakeVoyage()
        p = VoyageEmbedder(client=c, api_key="k")
        p.embed(["a"])
        p.embed_query(["a"])
        self.assertEqual([call["input_type"] for call in c.calls],
                         ["document", "query"])

    def test_finance_model_is_not_sent_an_output_dimension(self):
        # voyage-finance-2 is 1024-only and rejects the parameter
        c = _FakeVoyage()
        VoyageEmbedder("voyage-finance-2", client=c, api_key="k").embed(["a"])
        self.assertEqual(c.calls[0]["model"], "voyage-finance-2")
        self.assertNotIn("output_dimension", c.calls[0])

    def test_general_models_are_pinned_to_the_shared_width(self):
        c = _FakeVoyage()
        VoyageEmbedder("voyage-4-large", client=c, api_key="k").embed(["a"])
        self.assertEqual(c.calls[0]["output_dimension"], EMBEDDING_DIM)

    def test_the_default_is_voyage_4_large(self):
        c = _FakeVoyage()
        VoyageEmbedder(client=c, api_key="k").embed(["a"])
        self.assertEqual((c.calls[0]["model"], c.calls[0]["output_dtype"]),
                         ("voyage-4-large", "float"))

    def test_batches_respect_the_thousand_text_limit(self):
        c = _FakeVoyage()
        out = VoyageEmbedder(client=c, api_key="k").embed(["x"] * 2500)
        self.assertEqual([len(call["texts"]) for call in c.calls],
                         [1000, 1000, 500])
        self.assertEqual(len(out), 2500)

    def test_missing_key_names_the_variable_and_the_way_out(self):
        with self.assertRaises(EmbeddingError) as ctx:
            VoyageEmbedder(api_key="").embed(["a"])
        msg = str(ctx.exception)
        self.assertTrue("VOYAGE_API_KEY" in msg or "voyageai" in msg)

    def test_no_texts_makes_no_call(self):
        c = _FakeVoyage()
        self.assertEqual(VoyageEmbedder(client=c, api_key="k").embed([]), [])
        self.assertEqual(c.calls, [])


class _FakeOpenAI:
    def __init__(self, shuffle=False):
        self.calls = []
        self.shuffle = shuffle

    @property
    def embeddings(self):
        outer = self

        class _E:
            def create(self, **kw):
                outer.calls.append(kw)
                items = [type("D", (), {"index": i, "embedding": vec(fill=i)})()
                         for i in range(len(kw["input"]))]
                if outer.shuffle:
                    items.reverse()
                return type("R", (), {"data": items})()
        return _E()


class OpenAI(unittest.TestCase):
    def test_the_shared_width_is_requested_explicitly(self):
        # text-embedding-3-large is 3072 by default; `dimensions` shortens it
        c = _FakeOpenAI()
        OpenAIEmbedder(client=c, api_key="k").embed(["a"])
        self.assertEqual(c.calls[0]["dimensions"], EMBEDDING_DIM)
        self.assertEqual(c.calls[0]["model"], "text-embedding-3-large")

    def test_results_are_ordered_by_index_not_by_arrival(self):
        c = _FakeOpenAI(shuffle=True)
        out = OpenAIEmbedder(client=c, api_key="k").embed(["a", "b", "c"])
        # fill == index, so a misordered result is visible in the values
        self.assertEqual([v[0] for v in out], [0.0, 1.0, 2.0])

    def test_batches_are_chunked(self):
        c = _FakeOpenAI()
        out = OpenAIEmbedder(client=c, api_key="k").embed(["x"] * 300)
        self.assertEqual([len(call["input"]) for call in c.calls], [128, 128, 44])
        self.assertEqual(len(out), 300)


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = str(body)

    def json(self):
        return self._body


class _FakeGeminiHTTP:
    """Answers batchEmbedContents with one vector per request, after playing
    back any scripted failures."""

    def __init__(self, *failures, dim=EMBEDDING_DIM, fill=0.1):
        self.failures = list(failures)
        self.calls: list[tuple[str, dict, dict]] = []
        self.dim, self.fill = dim, fill

    def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        if self.failures:
            return self.failures.pop(0)
        return _Resp(200, {"embeddings": [{"values": vec(self.dim, self.fill)}
                                          for _ in json["requests"]],
                           "usageMetadata": {"promptTokenCount": 7}})


def _rate_limited(seconds):
    return _Resp(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                 "message": "Quota exceeded",
                                 "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                              "retryDelay": f"{seconds}s"}]}})


def _overloaded():
    return _Resp(503, {"error": {"code": 503, "status": "UNAVAILABLE",
                                 "message": "high demand"}})


class Gemini(unittest.TestCase):
    def embedder(self, http, **kw):
        self.slept: list[float] = []
        return GeminiEmbedder(api_key="test-key", http=http, sleep=self.slept.append, **kw)

    def test_the_task_goes_in_the_text_for_gemini_embedding_2(self):
        """It takes no taskType; Google's docs give these prefixes instead."""
        http = _FakeGeminiHTTP()
        e = self.embedder(http)
        e.embed(["Export controls restrict shipments."])
        e.embed_query(["export rules"])
        (_, doc, _), (_, query, _) = http.calls
        [d], [q] = doc["requests"], query["requests"]
        self.assertEqual(d["content"]["parts"][0]["text"],
                         "title: none | text: Export controls restrict shipments.")
        self.assertEqual(q["content"]["parts"][0]["text"],
                         "task: search result | query: export rules")
        self.assertNotIn("taskType", d)
        self.assertEqual((d["model"], d["outputDimensionality"]),
                         ("models/gemini-embedding-2", EMBEDDING_DIM))

    def test_the_key_goes_in_a_header_never_the_url(self):
        http = _FakeGeminiHTTP()
        self.embedder(http).embed(["x"])
        url, _, headers = http.calls[0]
        self.assertEqual(headers, {"x-goog-api-key": "test-key"})
        self.assertNotIn("test-key", url)
        self.assertTrue(url.endswith("/v1beta/models/gemini-embedding-2:batchEmbedContents"))

    def test_gemini_embedding_001_uses_task_types_and_is_normalised(self):
        http = _FakeGeminiHTTP(fill=0.5)
        e = self.embedder(http, model="gemini-embedding-001")
        [v] = e.embed(["x"])
        e.embed_query(["y"])
        self.assertEqual([c[1]["requests"][0]["taskType"] for c in http.calls],
                         ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"])
        self.assertEqual(http.calls[0][1]["requests"][0]["content"]["parts"][0]["text"], "x")
        self.assertAlmostEqual(sum(x * x for x in v), 1.0, places=6)

    def test_one_request_per_text_batched_by_the_hundred(self):
        """Several parts in one request would be averaged into one vector."""
        http = _FakeGeminiHTTP()
        out = self.embedder(http).embed([f"t{i}" for i in range(250)])
        self.assertEqual(len(out), 250)
        self.assertEqual([len(c[1]["requests"]) for c in http.calls], [100, 100, 50])

    def test_the_worker_waits_out_a_rate_limit_honouring_the_hint(self):
        http = _FakeGeminiHTTP(_rate_limited(3), _overloaded())
        self.assertEqual(len(self.embedder(http).embed(["x"])), 1)
        self.assertEqual(self.slept, [3.0, 2.0], "the hint, then backoff")

    def test_a_search_gets_one_quick_retry_then_a_clear_error(self):
        http = _FakeGeminiHTTP(_overloaded(), _overloaded())
        with self.assertRaises(EmbeddingError) as ctx:
            self.embedder(http).embed_query(["q"])
        self.assertIn("HTTP 503", str(ctx.exception))
        self.assertEqual((len(http.calls), self.slept), (2, [1.0]))

    def test_a_search_does_not_wait_a_long_hint(self):
        http = _FakeGeminiHTTP(_rate_limited(30))
        with self.assertRaises(EmbeddingError):
            self.embedder(http).embed_query(["q"])
        self.assertEqual(self.slept, [])

    def test_a_bad_request_is_not_retried(self):
        http = _FakeGeminiHTTP(_Resp(400, {"error": {"message": "bad dimension"}}))
        with self.assertRaises(EmbeddingError) as ctx:
            self.embedder(http).embed(["x"])
        self.assertIn("bad dimension", str(ctx.exception))
        self.assertEqual(len(http.calls), 1)

    def test_a_wrong_width_is_refused(self):
        with self.assertRaises(EmbeddingError):
            self.embedder(_FakeGeminiHTTP(dim=768)).embed(["x"])

    def test_missing_key_names_the_variable_and_the_way_out(self):
        old = os.environ.pop("GEMINI_API_KEY", None)
        try:
            with self.assertRaises(EmbeddingError) as ctx:
                GeminiEmbedder(http=_FakeGeminiHTTP()).embed(["x"])
            self.assertIn("GEMINI_API_KEY", str(ctx.exception))
            self.assertIn("EMBEDDING_PROVIDER", str(ctx.exception))
        finally:
            if old is not None:
                os.environ["GEMINI_API_KEY"] = old

    def test_no_texts_makes_no_call(self):
        http = _FakeGeminiHTTP()
        self.assertEqual(self.embedder(http).embed([]), [])
        self.assertEqual(http.calls, [])


class Hashing(unittest.TestCase):
    def test_is_deterministic(self):
        e = HashingEmbedder(dim=64)
        self.assertEqual(e.embed(["capital expenditure"]),
                         e.embed(["capital expenditure"]))

    def test_vectors_are_unit_length(self):
        v = HashingEmbedder(dim=64).embed(["data centre capacity expansion"])[0]
        self.assertAlmostEqual(sum(x * x for x in v) ** 0.5, 1.0, places=6)

    def test_empty_text_does_not_divide_by_zero(self):
        self.assertEqual(HashingEmbedder(dim=8).embed([""])[0], [0.0] * 8)


if __name__ == "__main__":
    unittest.main()
