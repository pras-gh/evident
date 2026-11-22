"""Answers: market questions from the live web, company questions from filings.

"Why is Nvidia down" needs current information, which only a web search can
give truthfully; Gemini's Google Search grounding provides it on paid keys.
"What are Nvidia's China risks" is answered from the passages search found,
each claim citing one. These check the gateway's web-search plumbing, the
two answers, and the endpoint that chooses between them — including what it
says when Google refuses web search for want of billing, as it did live.
"""
from __future__ import annotations

import json
import unittest
import uuid
from datetime import date
from decimal import Decimal

import httpx

from parallx_ai.answer import (AnswerIncomplete, Passage, answer_from_filings,
                               answer_from_web, check_citations)
from parallx_ai.gateway import AIContext, AllProvidersFailed, Gateway, LLMRequest, MemoryStore, TaskType
from parallx_ai.gateway.config import from_dict
from parallx_ai.gateway.providers.base import ErrorKind, ProviderError
from parallx_ai.gateway.providers.gemini import GeminiProvider
from parallx_ai.gateway.types import Completion, Usage
from test_ai_gateway import RAW, Script, ok

ORG = uuid.uuid4()
CTX = AIContext(organization_id=ORG, source="test")


class WebScript(Script):
    web_search = True


def grounded(text="Shares fell after export curbs were reported.", searches=2):
    return Completion(text=text, stop="complete",
                      usage=Usage(input_tokens=500, output_tokens=200, search_queries=searches),
                      sources=[{"title": "reuters.com", "uri": "https://example.test/r"}],
                      search_suggestions="<div>suggestions</div>")


def billing_refusal():
    return ProviderError(ErrorKind.QUOTA_EXHAUSTED,
                         "RESOURCE_EXHAUSTED: You exceeded your current quota, please check "
                         "your plan and billing details.", status=429)


def gateway(gemini, anthropic=None, per_search=0.014):
    raw = json.loads(json.dumps(RAW))
    raw["limits"]["max_output_tokens"] = 4000
    for p in raw["prices"]:
        if p["model"] == "gemini:flash":
            p["per_search"] = per_search
    store = MemoryStore(orgs={ORG: {}})
    g = Gateway(from_dict(raw, today=date(2026, 11, 22)),
                {"gemini": gemini, "groq": Script("groq"),
                 "anthropic": anthropic or Script("anthropic")}, store, sleep=lambda s: None)
    return g, store


def web_request(**over):
    return LLMRequest(task=TaskType.RESEARCH_SYNTHESIS, system="s", user="why is nvda down",
                      max_output_tokens=500, web_search=True, **over)


class GatewayWebSearch(unittest.TestCase):
    def test_sources_and_suggestions_come_back_and_searches_are_priced(self):
        g, store = gateway(WebScript("gemini", grounded()))
        r = g.complete(web_request(), CTX)
        self.assertEqual(r.sources, [{"title": "reuters.com", "uri": "https://example.test/r"}])
        self.assertEqual(r.search_suggestions, "<div>suggestions</div>")
        # 500 in at $0.30/M + 200 out at $2.50/M + 2 searches at $0.014
        self.assertEqual(r.cost_usd, Decimal("0.028650"))
        self.assertEqual(store.rows[-1].estimated_cost_usd, Decimal("0.028650"))

    def test_only_providers_that_can_search_are_asked(self):
        anthropic = Script("anthropic", ok())
        g, _ = gateway(WebScript("gemini", billing_refusal()), anthropic=anthropic)
        with self.assertRaises(AllProvidersFailed) as cm:
            g.complete(web_request(), CTX)
        self.assertEqual(anthropic.calls, [], "Claude has no web search adapter")
        self.assertIn("billing", str(cm.exception))

    def test_a_refused_web_search_does_not_stop_the_model_answering_otherwise(self):
        gemini = WebScript("gemini", billing_refusal(), ok(text="plain"))
        g, _ = gateway(gemini)
        with self.assertRaises(AllProvidersFailed):
            g.complete(web_request(), CTX)
        self.assertEqual(g.breaker.state("gemini:flash+web"), "open")
        r = g.complete(LLMRequest(task=TaskType.RESEARCH_SYNTHESIS, system="s", user="u",
                                  max_output_tokens=500), CTX)
        self.assertEqual((r.provider, r.text), ("gemini", "plain"))

    def test_a_web_answer_is_never_cached(self):
        from parallx_ai.gateway import CacheSpec
        gemini = WebScript("gemini", grounded(), grounded())
        g, store = gateway(gemini)
        spec = CacheSpec("x", "p@1", shared=True)
        g.complete(web_request(cache=spec), CTX)
        g.complete(web_request(cache=spec), CTX)
        self.assertEqual(len(gemini.calls), 2)
        self.assertEqual(store.cache, {})

    def test_a_web_request_is_never_served_a_cached_answer(self):
        """Even one stored by the same question asked without web search:
        yesterday's answer is the wrong one for "why is it down today"."""
        from parallx_ai.gateway import CacheSpec
        spec = CacheSpec("x", "p@1", shared=True)
        gemini = WebScript("gemini", ok(text="from memory"), grounded())
        g, store = gateway(gemini)
        plain = LLMRequest(task=TaskType.RESEARCH_SYNTHESIS, system="s", user="why is nvda down",
                           max_output_tokens=500, cache=spec)
        g.complete(plain, CTX)
        self.assertEqual(len(store.cache), 1)
        r = g.complete(web_request(cache=spec), CTX)
        self.assertEqual((r.cached, r.text, len(gemini.calls)),
                         (False, "Shares fell after export curbs were reported.", 2))

    def test_the_spending_check_assumes_several_searches(self):
        raw = json.loads(json.dumps(RAW))
        raw["prices"][0]["per_search"] = 0.014
        price = from_dict(raw, today=date(2026, 11, 22)).prices[0]
        self.assertEqual(price.worst_case(0, 0, searches=5), Decimal("0.070000"))


class GeminiAdapter(unittest.TestCase):
    def provider(self, handler):
        return GeminiProvider("k", transport=httpx.MockTransport(handler))

    def test_web_search_sends_the_tool_and_no_schema(self):
        body = GeminiProvider("k").body(
            web_request(json_schema={"type": "object"}), {})
        self.assertEqual(body["tools"], [{"google_search": {}}])
        self.assertNotIn("responseMimeType", body["generationConfig"])

    def test_grounding_metadata_is_read(self):
        reply = {"candidates": [{"content": {"parts": [{"text": "Shares fell."}]},
                                 "finishReason": "STOP",
                                 "groundingMetadata": {
                                     "webSearchQueries": ["nvidia stock today", " "],
                                     "groundingChunks": [{"web": {"uri": "https://a.test",
                                                                  "title": "a.test"}}, {}],
                                     "searchEntryPoint": {"renderedContent": "<b>chips</b>"}}}],
                 "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}
        p = self.provider(lambda req: httpx.Response(200, json=reply))
        c = p.complete("m", web_request(), timeout=5, params={})
        self.assertEqual((c.text, c.usage.search_queries), ("Shares fell.", 1))
        self.assertEqual(c.sources, [{"title": "a.test", "uri": "https://a.test"}])
        self.assertEqual(c.search_suggestions, "<b>chips</b>")

    def test_a_free_key_asking_for_web_search_is_a_quota_not_a_rate_limit(self):
        """The body Google sent live: no retry time, no quota detail."""
        body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                          "message": "You exceeded your current quota, please check your "
                                     "plan and billing details.",
                          "details": [{"@type": "type.googleapis.com/google.rpc.Help"}]}}
        p = self.provider(lambda req: httpx.Response(429, json=body))
        with self.assertRaises(ProviderError) as cm:
            p.complete("m", web_request(), timeout=5, params={})
        self.assertEqual(cm.exception.kind, ErrorKind.QUOTA_EXHAUSTED)


class Answers(unittest.TestCase):
    PASSAGES = [Passage("Export controls restrict shipments to China.", "NVIDIA 10-K, p. 26"),
                Passage("China questioned the H20's security.", "NVIDIA 10-K, p. 27")]

    def test_citations_to_passages_not_given_are_removed(self):
        text, cited, dropped = check_citations(
            "Export controls bite [1][7]. China doubts the H20 [2] [1].", 2)
        self.assertEqual(text, "Export controls bite [1]. China doubts the H20 [2] [1].")
        self.assertEqual((cited, dropped), ([1, 2], [7]))

    def test_a_filings_answer_sees_numbered_passages_and_cites_them(self):
        seen = []

        class Peek(Script):
            def complete(self, model, request, *, timeout, params):
                seen.append(request)
                return super().complete(model, request, timeout=timeout, params=params)

        g, _ = gateway(Peek("gemini", ok(text="Export controls [1][9].")))
        a = answer_from_filings("china risks", self.PASSAGES, CTX, g)
        self.assertIn("[1] (NVIDIA 10-K, p. 26) Export controls", seen[0].user)
        self.assertFalse(seen[0].web_search)
        self.assertEqual((a.mode, a.text, a.cited, a.dropped),
                         ("filings", "Export controls [1].", [1], [9]))

    def test_a_cut_off_answer_is_not_returned(self):
        g, _ = gateway(Script("gemini", ok(text="Export controls effec", stop="max_tokens")))
        with self.assertRaises(AnswerIncomplete):
            answer_from_filings("china risks", self.PASSAGES, CTX, g)

    def test_a_web_answer_carries_its_sources(self):
        g, _ = gateway(WebScript("gemini", grounded()))
        a = answer_from_web("why is nvda down", CTX, g)
        self.assertEqual((a.mode, a.sources[0]["uri"], a.search_suggestions),
                         ("web", "https://example.test/r", "<div>suggestions</div>"))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- the endpoint
from datetime import datetime, timezone  # noqa: E402

from test_evidence_e2e import _ApiMixin, _reset_schema  # noqa: E402
from test_ingest_e2e import DSN  # noqa: E402


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class Endpoint(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    """POST /v1/ask, with chunks on record and a scripted Gemini."""

    def setUp(self):
        from parallx_db import session_scope
        from parallx_db.repositories import replace_chunks, upsert_company, upsert_document
        from parallx_parser.models import chunk_hash
        import api.routers.ask as ask
        _reset_schema()
        ask._web_refused_at = None
        self.addCleanup(setattr, ask, "_web_refused_at", None)
        with session_scope(DSN) as db:
            co = upsert_company(db, cik="0001045810", name="NVIDIA CORP", ticker="NVDA")
            db.flush()
            doc, _ = upsert_document(
                db, company_id=co.id, accession="0001045810-26-000021", form_type="10-K",
                filed_at=date(2026, 2, 25),
                published_at=datetime(2026, 2, 25, 21, 0, tzinfo=timezone.utc),
                source_url="https://example.invalid/f.htm", source_format="html",
                content_sha256="b" * 64, fiscal_period="FY2026", page_count=85)
            db.flush()
            texts = [(26, "Export controls restrict shipments of our products to China."),
                     (27, "China's government questioned whether the H20 is secure.")]
            replace_chunks(db, document_id=doc.id, chunks=[
                dict(chunk_hash=chunk_hash(company_id="0001045810",
                                           document_accession="0001045810-26-000021",
                                           page_number=page, text=t),
                     paragraph_ids=[f"{page}_1"], ordinal=i, page_number=page,
                     section_title="Item 1A. Risk Factors", section_path=["Item 1A"],
                     text=t, char_count=len(t), token_estimate=len(t) // 4)
                for i, (page, t) in enumerate(texts)])
            db.flush()
            from parallx_db import Chunk
            from sqlalchemy import select
            self.chunk_ids = list(db.execute(select(Chunk.id).order_by(Chunk.ordinal)).scalars())

    def install(self, gemini):
        from parallx_ai.gateway import internal_organization_id, set_gateway
        raw = json.loads(json.dumps(RAW))
        raw["limits"]["max_output_tokens"] = 4000
        set_gateway(None)
        org = internal_organization_id(DSN)
        g = Gateway(from_dict(raw, today=date(2026, 11, 22)),
                    {"gemini": gemini, "groq": Script("groq"), "anthropic": Script("anthropic")},
                    MemoryStore(orgs={org: {}}), sleep=lambda s: None)
        set_gateway(g)
        self.addCleanup(set_gateway, None)
        return gemini

    async def ask(self, **body):
        async with await self._client() as c:
            r = await c.post("/v1/ask", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    async def test_a_market_question_is_answered_from_the_web(self):
        self.install(WebScript("gemini", grounded()))
        a = await self.ask(query="why is nvidia down", kind="market", chunk_ids=self.chunk_ids)
        self.assertEqual((a["mode"], a["answer"]), ("web", "Shares fell after export curbs were reported."))
        self.assertEqual(a["sources"], [{"title": "reuters.com", "uri": "https://example.test/r"}])
        self.assertEqual(a["search_suggestions"], "<div>suggestions</div>")
        self.assertEqual(a["citations"], [])

    async def test_without_billing_it_says_so_and_answers_from_the_filings(self):
        gemini = self.install(WebScript("gemini", billing_refusal(),
                                        ok(text="Export controls weigh on China sales [1]."),
                                        ok(text="Export controls [1].")))
        a = await self.ask(query="why is nvidia down", kind="market", chunk_ids=self.chunk_ids)
        self.assertEqual(a["mode"], "filings")
        self.assertIn("need billing enabled", a["notice"])
        self.assertIn("Here is what the SEC filings say instead.", a["notice"])
        self.assertEqual([(c["n"], c["chunk_id"], c["paragraph_id"]) for c in a["citations"]],
                         [(1, self.chunk_ids[0], "26_1")])
        # remembered: the next market question doesn't ask Google again
        again = await self.ask(query="why is the market down", kind="market",
                               chunk_ids=self.chunk_ids)
        self.assertIn("need billing enabled", again["notice"])
        self.assertEqual(len(gemini.calls), 3, "refusal, answer, answer — no second web try")

    async def test_a_filings_question_is_answered_from_the_passages(self):
        self.install(Script("gemini", ok(text="Export controls [1]; H20 doubts [2] [5].")))
        a = await self.ask(query="nvidia china risks", kind="filings", chunk_ids=self.chunk_ids)
        self.assertEqual((a["mode"], a["notice"]), ("filings", None))
        self.assertEqual(a["answer"], "Export controls [1]; H20 doubts [2].")
        self.assertEqual([c["n"] for c in a["citations"]], [1, 2])
        self.assertEqual(a["citations"][1]["citation"], "p. 27 · Item 1A. Risk Factors")

    async def test_nothing_to_answer_from_is_no_answer(self):
        self.install(Script("gemini"))
        a = await self.ask(query="wesco international", kind="filings", chunk_ids=[])
        self.assertEqual((a["mode"], a["answer"], a["notice"]), ("none", None, None))
