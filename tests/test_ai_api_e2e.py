"""The AI gateway through the API, against real Postgres.

    POST /v1/document/{id}/summary      paid inference, attributed, limited, cached
    GET  /v1/me
    GET  /v1/ai-usage/summary           scoped by role
    GET  /v1/ai-usage/limits

The real Gemini adapter is used, over mocked HTTP, so the request goes through
everything a production one does — auth, limits, cache, router, adapter, cost,
the usage log — except the network. The filing is the real FY2026 NVIDIA
Risk Factors corpus.

Skipped unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import json
import re
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from test_evidence_e2e import _ApiMixin
from test_ingest_e2e import DSN
from test_timeline_e2e import _ingest_and_extract

SUMMARY_IN, SUMMARY_OUT = 21_000, 300      # tokens the mocked provider reports


class FakeGemini:
    """generateContent, answering from the paragraph ids it was sent."""

    def __init__(self):
        self.calls = 0
        self.fail_with: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"error": {"message": "down", "status": "UNAVAILABLE"}})
        user = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        ids = re.findall(r"^\[([^\]]+)\]", user, re.M)
        answer = {"summary": "NVIDIA describes export controls on its data center GPUs.",
                  "key_points": [{"point": "Export controls restrict sales to China.",
                                  "paragraph_ids": ids[:2]},
                                 {"point": "Invented.", "paragraph_ids": ["999_9"]}]}
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": SUMMARY_IN, "candidatesTokenCount": SUMMARY_OUT}})


def _people():
    """Two customers, roles in each, and a platform admin; returns keys by name."""
    from parallx_db import ApiKey, Organization, User, session_scope
    from parallx_db.keys import display_prefix, hash_key, new_key
    from parallx_ai.gateway import internal_organization_id, set_gateway
    set_gateway(None)                          # forget any cached internal org id
    keys, ids = {}, {}
    internal = internal_organization_id(DSN)
    with session_scope(DSN) as db:
        acme = Organization(slug="acme", name="Acme Capital")
        beta = Organization(slug="beta", name="Beta Research")
        rogue = Organization(slug="rogue", name="Rogue")
        db.add_all([acme, beta, rogue])
        db.flush()
        for name, org, role in (("acme_member", acme.id, "member"),
                                ("acme_member2", acme.id, "member"),
                                ("acme_admin", acme.id, "org_admin"),
                                ("beta_member", beta.id, "member"),
                                ("platform", internal, "platform_admin"),
                                ("rogue_admin", rogue.id, "platform_admin")):
            user = User(organization_id=org, email=f"{name}@example.com", role=role)
            db.add(user)
            db.flush()
            key = new_key()
            db.add(ApiKey(user_id=user.id, key_hash=hash_key(key), prefix=display_prefix(key)))
            keys[name], ids[name] = key, user.id
        ids["acme"], ids["beta"] = acme.id, beta.id
    return keys, ids


def _gateway(fake: FakeGemini, groq_status: int = 503):
    from parallx_ai.gateway import Gateway, SqlStore, set_gateway
    from parallx_ai.gateway.config import from_dict
    from parallx_ai.gateway.providers.gemini import GeminiProvider
    from parallx_ai.gateway.providers.groq import GroqProvider
    from parallx_ai.gateway.providers.anthropic import AnthropicProvider
    from test_ai_gateway import RAW
    raw = json.loads(json.dumps(RAW))
    raw["limits"].update(max_input_tokens=200_000, max_output_tokens=16_000)
    raw["gateway"].update(max_attempts=1)
    down = httpx.MockTransport(lambda r: httpx.Response(groq_status, json={"error": {"message": "down"}}))
    g = Gateway(from_dict(raw, today=date.today()),
                {"gemini": GeminiProvider("test-gemini-key", transport=httpx.MockTransport(fake)),
                 "groq": GroqProvider("test-groq-key", transport=down),
                 "anthropic": AnthropicProvider("test-anthropic-key", transport=down)},
                SqlStore(DSN), sleep=lambda s: None)
    set_gateway(g)
    return g


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class AiApi(_ApiMixin, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        _ingest_and_extract(filings=3)
        cls.keys, cls.ids = _people()
        from sqlalchemy import select
        from parallx_db import Document, session_scope
        with session_scope(DSN) as db:
            docs = db.execute(select(Document).order_by(Document.filed_at.desc())).scalars().all()
            cls.doc_ids = [d.id for d in docs]

    def setUp(self):
        from parallx_db import AIResponseCache, AIUsageLog, session_scope
        with session_scope(DSN) as db:          # each test starts with no usage
            db.query(AIUsageLog).delete()
            db.query(AIResponseCache).delete()
        self.fake = FakeGemini()
        self.gw = _gateway(self.fake)

    def tearDown(self):
        from parallx_ai.gateway import set_gateway
        set_gateway(None)

    async def call(self, method, path, who=None, **kw):
        headers = dict(kw.pop("headers", {}))
        if who:
            headers["Authorization"] = f"Bearer {self.keys[who]}"
        async with await self._client() as c:
            if not who and "Authorization" not in headers:
                c.headers.pop("Authorization", None)     # anonymous, not the suite's staff key
            return await c.request(method, path, headers=headers, **kw)

    def rows(self):
        from sqlalchemy import select
        from parallx_db import AIUsageLog, session_scope
        with session_scope(DSN) as db:
            return [(r.status, r.organization_id, r.user_id, r.task_type, r.provider,
                     r.input_tokens, r.output_tokens, r.estimated_cost_usd)
                    for r in db.execute(select(AIUsageLog).order_by(AIUsageLog.created_at)).scalars()]

    # ------------------------------------------------------------ auth
    async def test_paid_and_private_endpoints_need_a_valid_key(self):
        for path, method in ((f"/v1/document/{self.doc_ids[0]}/summary", "POST"),
                             ("/v1/ai-usage/summary", "GET"), ("/v1/me", "GET")):
            with self.subTest(path):
                self.assertEqual((await self.call(method, path)).status_code, 401)
                r = await self.call(method, path, headers={"Authorization": "Bearer plx_nope"})
                self.assertEqual(r.status_code, 401)
        self.assertEqual(self.fake.calls, 0)

    async def test_me(self):
        r = await self.call("GET", "/v1/me", "acme_admin")
        self.assertEqual((r.json()["role"], r.json()["organization"]["slug"]), ("org_admin", "acme"))

    async def test_a_platform_role_outside_the_internal_org_is_refused(self):
        self.assertEqual((await self.call("GET", "/v1/me", "rogue_admin")).status_code, 403)

    async def test_keys_are_stored_only_as_hashes(self):
        from sqlalchemy import text
        from parallx_db import session_scope
        with session_scope(DSN) as db:
            dump = " ".join(str(r) for r in db.execute(text("select * from api_keys")).all())
        for key in self.keys.values():
            self.assertNotIn(key, dump)

    # ---------------------------------------------------------- summary
    async def test_a_summary_is_attributed_costed_and_cited(self):
        r = await self.call("POST", f"/v1/document/{self.doc_ids[0]}/summary", "acme_member")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["provider"], body["model"], body["cached"]), ("gemini", "flash", False))
        [point] = body["key_points"]              # the invented citation was dropped
        self.assertEqual(len(point["citations"]), 2)
        for c in point["citations"]:
            self.assertEqual(c["page"], int(c["paragraph_id"].split("_")[0]))
            self.assertIsInstance(c["chunk_id"], int)
        # 21,000 in at $0.30 + 300 out at $2.50, per million
        self.assertEqual(Decimal(str(body["cost_usd"])), Decimal("0.007050"))
        [row] = self.rows()
        self.assertEqual(row, ("success", self.ids["acme"], self.ids["acme_member"],
                               "filing_summarization", "gemini", SUMMARY_IN, SUMMARY_OUT,
                               Decimal("0.007050")))

    async def test_a_second_request_for_the_same_summary_costs_nothing(self):
        await self.call("POST", f"/v1/document/{self.doc_ids[0]}/summary", "acme_member")
        again = (await self.call("POST", f"/v1/document/{self.doc_ids[0]}/summary", "acme_member")).json()
        other = (await self.call("POST", f"/v1/document/{self.doc_ids[0]}/summary", "beta_member")).json()
        self.assertEqual(self.fake.calls, 1)
        self.assertEqual((again["cached"], Decimal(str(again["cost_usd"]))), (True, Decimal(0)))
        self.assertTrue(other["cached"], "public filing text: shared across organizations")
        self.assertEqual([(s, o) for s, o, *_ in self.rows()],
                         [("success", self.ids["acme"]), ("cached", self.ids["acme"]),
                          ("cached", self.ids["beta"])])

    async def test_a_usage_limit_is_a_clear_429_and_nothing_is_called(self):
        from parallx_db import Organization, session_scope
        with session_scope(DSN) as db:
            db.get(Organization, self.ids["acme"]).daily_requests_per_user = 1
        try:
            ok = await self.call("POST", f"/v1/document/{self.doc_ids[0]}/summary", "acme_member")
            self.assertEqual(ok.status_code, 200)
            r = await self.call("POST", f"/v1/document/{self.doc_ids[1]}/summary", "acme_member")
            self.assertEqual(r.status_code, 429)
            body = r.json()
            self.assertEqual((body["error"], body["limit"], body["allowed"], body["current"]),
                             ("usage_limit", "daily_requests_per_user", 1, 1))
            self.assertGreater(int(r.headers["retry-after"]), 0)
            self.assertEqual(self.fake.calls, 1)
            self.assertEqual(self.rows()[-1][0], "blocked")
            # another user in the same organization still has their own allowance
            r2 = await self.call("POST", f"/v1/document/{self.doc_ids[1]}/summary", "acme_member2")
            self.assertEqual(r2.status_code, 200)
        finally:
            with session_scope(DSN) as db:
                db.get(Organization, self.ids["acme"]).daily_requests_per_user = None

    async def test_when_no_provider_answers_the_caller_gets_a_503_with_the_request_id(self):
        self.fake.fail_with = 503
        r = await self.call("POST", f"/v1/document/{self.doc_ids[0]}/summary", "acme_member")
        self.assertEqual(r.status_code, 503)
        body = r.json()
        self.assertEqual(body["error"], "ai_unavailable")
        self.assertNotIn("down", body["message"], "provider detail stays in the logs")
        self.assertEqual([s for s, *_ in self.rows()], ["failed", "failed"])

    # ------------------------------------------------------------ scope
    async def _usage(self, who, **params):
        r = await self.call("GET", "/v1/ai-usage/summary", who, params=params)
        return r

    async def test_each_role_sees_exactly_its_own_scope(self):
        for who in ("acme_member", "acme_member2", "beta_member"):
            await self.call("POST", f"/v1/document/{self.doc_ids[0]}/summary", who)
        member = (await self._usage("acme_member")).json()
        admin = (await self._usage("acme_admin")).json()
        beta = (await self._usage("beta_member")).json()
        platform = (await self._usage("platform")).json()
        self.assertEqual(member["totals"]["requests"], 1)
        self.assertEqual(admin["totals"]["requests"], 2)
        self.assertEqual(beta["totals"]["requests"], 1)
        self.assertEqual(platform["totals"]["requests"], 3)
        self.assertEqual({r["label"] for r in platform["by_organization"]},
                         {"Acme Capital", "Beta Research"})
        self.assertEqual({r["label"] for r in beta["by_organization"]}, {"Beta Research"})
        self.assertEqual(admin["facets"]["organizations"], [], "only platform admins list customers")
        self.assertGreater(len(platform["facets"]["organizations"]), 2)

    async def test_asking_for_someone_elses_usage_is_refused_not_empty(self):
        cases = (("acme_member", {"organization_id": str(self.ids["beta"])}),
                 ("acme_admin", {"organization_id": str(self.ids["beta"])}),
                 ("acme_member", {"user_id": str(self.ids["acme_member2"])}))
        for who, params in cases:
            with self.subTest(who=who, params=params):
                self.assertEqual((await self._usage(who, **params)).status_code, 403)
        self.assertEqual((await self.call("GET", "/v1/ai-usage/limits", "beta_member",
                                          params={"organization_id": str(self.ids["acme"])})).status_code, 403)

    async def test_platform_admins_can_filter_by_customer(self):
        await self.call("POST", f"/v1/document/{self.doc_ids[0]}/summary", "beta_member")
        r = (await self._usage("platform", organization_id=str(self.ids["acme"]))).json()
        self.assertEqual(r["totals"]["requests"], 0)

    # ----------------------------------------------------------- numbers
    async def test_the_dashboard_numbers_add_up(self):
        """Rows with known values for one customer; every total checked."""
        from parallx_db import AIUsageLog, session_scope
        org, user = self.ids["acme"], self.ids["acme_member"]
        today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        a, b, c, d, e = (uuid.uuid4() for _ in range(5))
        spec = [  # (request, status, provider, model, in, out, cached, cost, days ago)
            (a, "success", "gemini", "flash", 1000, 100, 0, "0.0010", 0),
            (b, "failed", "gemini", "flash", 0, 0, 0, "0", 0),
            (b, "fallback", "groq", "oss", 2000, 200, 500, "0.0020", 0),
            (c, "failed", "gemini", "flash", 0, 0, 0, "0", 1),
            (c, "failed", "groq", "oss", 0, 0, 0, "0", 1),
            (d, "cached", "gemini", "flash", 0, 0, 0, "0", 1),
            (e, "blocked", "gemini", "flash", 0, 0, 0, "0", 40),
        ]
        with session_scope(DSN) as db:
            db.add_all(AIUsageLog(organization_id=org, user_id=user, provider=pv, model=m,
                                  task_type="filing_summarization", request_id=rid, status=st,
                                  input_tokens=i, output_tokens=o, cached_tokens=ca,
                                  estimated_cost_usd=Decimal(cost),
                                  created_at=today - timedelta(days=ago))
                       for rid, st, pv, m, i, o, ca, cost, ago in spec)
        r = (await self._usage("acme_admin", date_from=str((today - timedelta(days=60)).date()))).json()
        t = r["totals"]
        self.assertEqual((Decimal(str(t["cost_usd"])), t["requests"], t["attempts"]),
                         (Decimal("0.003"), 5, 5))
        self.assertEqual((t["input_tokens"], t["output_tokens"], t["cached_tokens"]), (3000, 300, 500))
        self.assertEqual((t["failed_requests"], t["failed_attempts"]), (1, 3))
        self.assertEqual((t["fallback_requests"], t["fallback_rate"]), (1, 0.5))
        self.assertEqual((t["cache_hits"], t["blocked_requests"]), (1, 1))
        # $0.003 over three answered requests (a, b from a provider; d from cache)
        self.assertEqual(Decimal(str(t["avg_cost_per_request"])), Decimal("0.001"))
        self.assertEqual({x["key"]: Decimal(str(x["cost_usd"])) for x in r["by_provider"]},
                         {"gemini": Decimal("0.001"), "groq": Decimal("0.002")})
        self.assertEqual(sum(p["requests"] for p in r["daily"]), 5)
        self.assertEqual({x["status"] for x in r["recent_failures"]}, {"failed", "fallback", "blocked"})
        # UTC on the wire, not the database session's zone
        self.assertTrue(all(x["created_at"].endswith(("Z", "+00:00")) for x in r["recent_failures"]))
        # filters narrow every number
        groq = (await self._usage("acme_admin", provider="groq",
                                  date_from=str((today - timedelta(days=60)).date()))).json()["totals"]
        self.assertEqual((groq["requests"], Decimal(str(groq["cost_usd"]))), (2, Decimal("0.002")))
        recent = (await self._usage("acme_admin", date_from=str(today.date()))).json()["totals"]
        self.assertEqual(recent["requests"], 2)

    async def test_limits_show_what_is_used_and_when_it_resets(self):
        await self.call("POST", f"/v1/document/{self.doc_ids[0]}/summary", "acme_member")
        r = (await self.call("GET", "/v1/ai-usage/limits", "acme_member")).json()
        by = {x["limit"]: x for x in r["limits"]}
        self.assertEqual(Decimal(str(by["daily_requests_per_user"]["used"])), 1)
        self.assertEqual(Decimal(str(by["monthly_tokens"]["used"])), SUMMARY_IN + SUMMARY_OUT)
        self.assertEqual(Decimal(str(by["monthly_spend_usd"]["used"])), Decimal("0.00705"))
        self.assertIsNotNone(by["monthly_spend_usd"]["resets_at"])


if __name__ == "__main__":
    unittest.main()
