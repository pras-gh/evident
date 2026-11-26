"""Accounts, free research credits, subscriptions — the journey, end to end at the API.

A person signs in with Google, researches NVIDIA (one credit), NVIDIA again
(none), Microsoft (the second), is stopped at Apple, buys Iix through
Checkout — granted only when a signed webhook says so — researches up to ten
companies, is stopped at the eleventh, moves to IIx (fifty), runs out of AI
queries, has a payment fail, then lapses — and keeps everything researched.

Nothing here uses real credentials. Sign-in tokens are Firebase-shaped JWTs
signed with a test key and checked by the real verifier; webhooks are Stripe
events signed with a test secret and checked by Stripe's own signature code;
the Stripe API calls go to a fake that records them. Database tests skip
unless TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from test_ingest_e2e import DSN

PROJECT = "parallx-test"
WHSEC = "whsec_test_secret"
PRICES = {"STRIPE_PRICE_IIX_MONTHLY": "price_iix_m", "STRIPE_PRICE_IIX_ANNUAL": "price_iix_y",
          "STRIPE_PRICE_IIX_ANALYST_MONTHLY": "price_iixa_m",
          "STRIPE_PRICE_IIX_ANALYST_ANNUAL": "price_iixa_y"}
COMPANIES = ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "LLY",
             "WMT", "XOM"]
NAMES = {"NVDA": "NVIDIA CORP", "MSFT": "MICROSOFT CORP", "AAPL": "Apple Inc."}


# ------------------------------------------------------------------ helpers


class Keys:
    """A key pair standing in for Google's: the verifier checks tokens it
    signs exactly as it checks Firebase's."""

    def __init__(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(self.key.public_key()).serial_number(1)
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=1)).sign(self.key, hashes.SHA256()))
        self.certs = {"k1": cert.public_bytes(serialization.Encoding.PEM).decode()}

    def token(self, *, uid="google-uid-1", provider="google.com", email="ada@gmail.com",
              verified=True, phone=None, aud=PROJECT, exp_in=3600, kid="k1", name="Ada"):
        import jwt
        now = int(time.time())
        claims = {"iss": f"https://securetoken.google.com/{aud}", "aud": aud, "sub": uid,
                  "iat": now, "exp": now + exp_in, "auth_time": now, "name": name,
                  "firebase": {"sign_in_provider": provider}}
        if email:
            claims.update(email=email, email_verified=verified)
        if phone:
            claims["phone_number"] = phone
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": kid})


KEYS = Keys()


def sign(payload: bytes, secret: str = WHSEC, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload.decode()}".encode(), hashlib.sha256)
    return f"t={ts},v1={sig.hexdigest()}"


def subscription_obj(sid, *, customer, user_id, price, interval="month", status="active",
                     start=None, end=None, cancel=False):
    start = start or int(time.time()) - 60
    end = end or start + 30 * 86400
    return {"id": sid, "object": "subscription", "customer": customer, "status": status,
            "cancel_at_period_end": cancel, "canceled_at": None,
            "metadata": {"user_id": str(user_id)},
            "items": {"data": [{"id": "si_1", "price": {"id": price,
                                                         "recurring": {"interval": interval}},
                                "current_period_start": start, "current_period_end": end}]}}


def event(type_, obj, *, eid=None, created=None):
    return {"id": eid or f"evt_{uuid.uuid4().hex[:12]}", "object": "event", "type": type_,
            "created": created or int(time.time()), "data": {"object": obj}}


def fake_provider():
    from api.billing import Checkout, StripeProvider

    class FakeStripe(StripeProvider):
        """Stripe's API, recorded; its webhook signatures and objects, real."""

        def __init__(self):
            super().__init__("sk_test_fake", WHSEC)
            self.calls, self.subs = [], {}

        def create_customer(self, *, user_id, email, phone, name):
            self.calls.append(("customer", user_id))
            return f"cus_{user_id[:8]}"

        def create_checkout(self, *, customer_id, price_id, user_id, plan_id, interval,
                            idempotency_key):
            self.calls.append(("checkout", plan_id, interval, price_id, idempotency_key))
            n = len(self.calls)
            return Checkout(f"cs_test_{n}", f"https://checkout.stripe.test/c/{n}")

        def change_plan(self, *, subscription_id, price_id, idempotency_key):
            self.calls.append(("change", subscription_id, price_id))

        def cancel(self, *, subscription_id):
            self.calls.append(("cancel", subscription_id))

        def portal(self, *, customer_id):
            return "https://billing.stripe.test/p/1"

        def subscription(self, subscription_id):
            return self.subscription_state(self.subs[subscription_id])

    return FakeStripe()


def corpus():
    """Twelve companies, a filing and an embedded passage each."""
    from parallx_db import Chunk, Company, Document, session_scope
    from parallx_retrieval.embed import HashingEmbedder
    emb = HashingEmbedder()
    with session_scope(DSN) as db:
        for n, t in enumerate(COMPANIES):
            co = Company(cik=f"{n + 1:010d}", ticker=t, name=NAMES.get(t, f"{t} Holdings Inc"))
            db.add(co)
            db.flush()
            doc = Document(company_id=co.id, accession=f"0000000000-26-{n:06d}", form_type="10-K",
                           filed_at=datetime(2026, 2, 1).date(),
                           published_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                           source_url="https://example.test", source_format="html",
                           content_sha256="0" * 64)
            db.add(doc)
            db.flush()
            text = f"{co.name} data center demand and capital allocation in fiscal 2026"
            db.add(Chunk(document_id=doc.id, chunk_hash=f"h-{t}", ordinal=0, paragraph_ids=["p1"],
                         text=text, char_count=len(text), token_estimate=20, page_number=1,
                         embedding=emb.embed([text])[0], embedding_provider="local",
                         embedding_model="hashing-v1"))


@unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
class _Api(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_evidence_e2e import _reset_schema
        from parallx_db import session_scope
        from parallx_db.accounts import ensure_plans
        from api.identity import FirebaseVerifier, set_verifier
        from api.billing import set_billing
        _reset_schema()
        with session_scope(DSN) as db:
            ensure_plans(db)
        corpus()
        env = {**PRICES, "EMBEDDING_PROVIDER": "hashing", "DATABASE_URL": DSN,
               "APP_URL": "http://localhost:3000", "SEC_USER_AGENT": "",
               "BILLING_UNSUPPORTED_COUNTRIES": "IN", "FREE_AI_QUERIES": "2"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        set_verifier(FirebaseVerifier(PROJECT, certs=KEYS.certs))
        self.addCleanup(set_verifier, None)
        self.stripe = fake_provider()
        set_billing(self.stripe)
        self.addCleanup(set_billing, None)
        import api.deps
        api.deps._factory = None

    async def client(self):
        import httpx
        from api.main import app
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                 headers={"origin": "http://localhost:3000"})

    async def sign_in(self, c, **kw):
        r = await c.post("/v1/auth/session", json={"id_token": KEYS.token(**kw)})
        self.assertEqual(r.status_code, 200, r.text)
        c.cookies.set("parallx_session", r.cookies["parallx_session"])
        return r.json()

    async def research(self, c, ticker, **kw):
        return await c.post("/v1/research/access", json={"ticker": ticker, **kw})

    async def usage(self, c):
        return (await c.get("/v1/me/usage")).json()

    async def hook(self, c, ev, *, secret=WHSEC):
        payload = json.dumps(ev).encode()
        return await c.post("/v1/billing/webhook", content=payload,
                            headers={"stripe-signature": sign(payload, secret),
                                     "content-type": "application/json"})

    async def subscribe(self, c, user_id, *, plan="investor", interval="month",
                        sid="sub_1", status="active", **kw):
        price = PRICES[{("investor", "month"): "STRIPE_PRICE_IIX_MONTHLY",
                        ("investor", "year"): "STRIPE_PRICE_IIX_ANNUAL",
                        ("analyst", "month"): "STRIPE_PRICE_IIX_ANALYST_MONTHLY",
                        ("analyst", "year"): "STRIPE_PRICE_IIX_ANALYST_ANNUAL"}[(plan, interval)]]
        obj = subscription_obj(sid, customer=f"cus_{str(user_id)[:8]}", user_id=user_id,
                               price=price, interval=interval, status=status, **kw)
        self.stripe.subs[sid] = obj
        return obj


class Journey(_Api):
    """The 22 steps."""

    async def test_the_whole_journey(self):
        async with await self.client() as c:
            # 1–2: a logged-out search is not answered; the page opens sign-in
            r = await c.post("/v1/search", json={"query": "nvidia"})
            self.assertEqual((r.status_code, r.json()["error"]), (401, "sign_in_required"))

            # 3: Google sign-up makes an account
            me = await self.sign_in(c)
            self.assertTrue(me["is_new"])
            uid = me["user"]["user_id"]
            u = await self.usage(c)
            self.assertEqual((u["plan_id"], u["companies_used"], u["companies_limit"]),
                             ("free", 0, 2))

            # 4–5: the search continues, and NVIDIA takes one credit
            r = await c.post("/v1/search", json={"query": "nvidia"})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["companies"][0]["access"], "granted")
            self.assertTrue(body["hits"])
            self.assertEqual((await self.usage(c))["companies_used"], 1)

            # 6–7: NVIDIA again — search, refresh, return — costs nothing
            r = await c.post("/v1/search", json={"query": "nvidia"})
            self.assertEqual(r.json()["companies"][0]["access"], "existing")
            for _ in range(3):
                self.assertEqual((await self.research(c, "NVDA")).json()["outcome"], "existing")
            self.assertEqual((await self.usage(c))["companies_used"], 1)

            # 8–9: Microsoft takes the second
            r = await self.research(c, "MSFT")
            self.assertEqual((r.status_code, r.json()["outcome"]), (200, "granted"))
            u = await self.usage(c)
            self.assertEqual((u["companies_used"], u["companies_limit"], u["companies_left"]),
                             (2, 2, 0))

            # 10–11: Apple needs an upgrade; search says so without searching it
            r = await self.research(c, "AAPL")
            self.assertEqual(r.status_code, 402)
            self.assertEqual((r.json()["error"], r.json()["reason"]),
                             ("upgrade_required", "free_credits_used"))
            r = await c.post("/v1/search", json={"query": "apple"})
            body = r.json()
            self.assertEqual(body["companies"][0]["access"], "upgrade_required")
            self.assertEqual(body["paywall"]["reason"], "free_credits_used")
            self.assertEqual(body["hits"], [])
            # what was researched stays open
            self.assertEqual((await c.get("/v1/companies/NVDA")).status_code, 200)
            self.assertEqual((await c.get("/v1/companies/AAPL")).status_code, 403)

            # 12: checkout — a URL to Stripe, and no plan yet
            r = await c.post("/v1/billing/checkout",
                             json={"plan_id": "investor", "interval": "month", "country": "US"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["url"].startswith("https://checkout.stripe.test/"))
            self.assertEqual(self.stripe.calls[-1][:4],
                             ("checkout", "investor", "month", "price_iix_m"))
            self.assertEqual((await c.get("/v1/me/subscription")).json()["plan_id"], "free",
                             "a redirect grants nothing")

            # 13: the signed webhook does
            sub = await self.subscribe(c, uid, plan="investor")
            done = {"id": "cs_test_1", "object": "checkout.session", "client_reference_id": uid,
                    "customer": sub["customer"], "subscription": "sub_1"}
            self.assertEqual((await self.hook(c, event("checkout.session.completed", done))
                              ).status_code, 200)
            s = (await c.get("/v1/me/subscription")).json()
            self.assertEqual((s["plan_id"], s["status"], s["billing_interval"]),
                             ("investor", "active", "month"))

            # 14: ten distinct companies (two of them from the free credits)
            for t in COMPANIES[2:10]:
                r = await self.research(c, t)
                self.assertEqual(r.status_code, 200, (t, r.text))
            u = await self.usage(c)
            self.assertEqual((u["companies_used"], u["companies_limit"]), (10, 10))

            # 15–16: the eleventh needs more room
            r = await self.research(c, COMPANIES[10])
            self.assertEqual((r.status_code, r.json()["reason"]), (402, "company_limit"))

            # 17–18: IIx — a plan change, applied when the webhook says so
            r = await c.post("/v1/billing/checkout",
                             json={"plan_id": "analyst", "interval": "month", "country": "US"})
            self.assertEqual(r.json()["mode"], "updated")
            self.assertEqual(self.stripe.calls[-1], ("change", "sub_1", "price_iixa_m"))
            self.assertEqual((await self.usage(c))["companies_limit"], 10, "not until confirmed")
            sub = await self.subscribe(c, uid, plan="analyst")
            await self.hook(c, event("customer.subscription.updated", sub))
            self.assertEqual((await self.usage(c))["companies_limit"], 50)
            self.assertEqual((await self.research(c, COMPANIES[10])).status_code, 200)

            # 19: AI queries stop at the plan's limit
            from parallx_db import session_scope
            from parallx_db.accounts import UsageLedger
            u = await self.usage(c)
            with session_scope(DSN) as db:
                for _ in range(u["ai_queries_limit"]):
                    db.add(UsageLedger(user_id=uuid.UUID(uid), kind="ai_query", quantity=1))
            r = await c.post("/v1/ask", json={"query": "What did NVIDIA say about demand?",
                                              "kind": "general"})
            self.assertEqual((r.status_code, r.json()["limit_reached"]), (200, True))

            # 20: a payment fails — the plan holds through the grace period …
            failing = await self.subscribe(c, uid, plan="analyst", status="past_due")
            await self.hook(c, event("invoice.payment_failed",
                                     {"id": "in_1", "object": "invoice", "subscription": "sub_1"}))
            s = (await c.get("/v1/me/subscription")).json()
            self.assertEqual((s["status"], s["source"], s["plan_id"]),
                             ("past_due", "grace", "analyst"))
            self.assertIsNotNone(s["grace_until"])
            # … and when it is cancelled for good, back to free
            ended = dict(failing, status="canceled")
            await self.hook(c, event("customer.subscription.deleted", ended,
                                     created=int(time.time()) + 5))
            s = (await c.get("/v1/me/subscription")).json()
            self.assertEqual((s["plan_id"], s["source"]), ("free", "free"))

            # 21: everything researched stays open; nothing new without a plan
            for t in COMPANIES[:11]:
                self.assertEqual((await c.get(f"/v1/companies/{t}")).status_code, 200, t)
            self.assertEqual((await self.research(c, COMPANIES[11])).status_code, 402)


class Credits(_Api):
    async def test_concurrent_requests_cannot_spend_one_credit_twice(self):
        async with await self.client() as c:
            await self.sign_in(c)
            await self.research(c, "NVDA")
            # one credit left; three new companies at once, and one company three times
            results = await asyncio.gather(*[self.research(c, t)
                                             for t in ("MSFT", "AAPL", "AMZN")])
            self.assertEqual(sorted(r.status_code for r in results), [200, 402, 402])
            self.assertEqual((await self.usage(c))["free_credits_used"], 2)

    async def test_the_same_company_at_once_is_one_credit(self):
        async with await self.client() as c:
            await self.sign_in(c)
            results = await asyncio.gather(*[self.research(c, "NVDA") for _ in range(4)])
            self.assertEqual(sorted(r.json()["outcome"] for r in results),
                             ["existing", "existing", "existing", "granted"])
            self.assertEqual((await self.usage(c))["free_credits_used"], 1)

    async def test_a_failed_research_job_gives_the_credit_back(self):
        from parallx_db import Company, session_scope
        from parallx_db.accounts import grant_access, settle_pending
        me = None
        async with await self.client() as c:
            me = await self.sign_in(c)
        with session_scope(DSN) as db:
            company = db.query(Company).filter_by(ticker="NVDA").one()
            r = grant_access(db, uuid.UUID(me["user"]["user_id"]), company.id, pending=True)
            self.assertEqual((r.outcome, r.status), ("granted", "pending"))
        with session_scope(DSN) as db:
            settle_pending(db, company.id, ok=False)
        async with await self.client() as c:
            c.cookies.set("parallx_session", "x")
            await self.sign_in(c)
            u = await self.usage(c)
            self.assertEqual((u["free_credits_used"], u["companies"]), (0, []))

    async def test_an_idempotency_key_charges_once(self):
        async with await self.client() as c:
            await self.sign_in(c)
            for _ in range(2):
                r = await self.research(c, "NVDA", idempotency_key="k-123")
                self.assertEqual(r.status_code, 200)
            self.assertEqual((await self.usage(c))["free_credits_used"], 1)


class SignIn(_Api):
    async def test_one_person_one_account_and_one_set_of_credits(self):
        async with await self.client() as c:
            first = await self.sign_in(c, uid="u-a", email="Ada.Lovelace+promo@gmail.com",
                                       provider="password")
            again = await self.sign_in(c, uid="u-b", email="adalovelace@googlemail.com",
                                       provider="google.com")
        self.assertTrue(first["is_new"])
        self.assertFalse(again["is_new"], "a Gmail spelling of the same address")
        self.assertEqual(first["user"]["user_id"], again["user"]["user_id"])

    async def test_a_phone_number_is_an_account(self):
        async with await self.client() as c:
            me = await self.sign_in(c, uid="u-phone", provider="phone", email=None,
                                    phone="+919812345678")
            self.assertEqual((me["user"]["phone"], me["user"]["email"]),
                             ("+919812345678", None))
            again = await self.sign_in(c, uid="u-phone", provider="phone", email=None,
                                       phone="+919812345678")
            self.assertFalse(again["is_new"])

    async def test_an_unverified_email_cannot_sign_in(self):
        async with await self.client() as c:
            r = await c.post("/v1/auth/session", json={"id_token": KEYS.token(
                provider="password", verified=False)})
        self.assertEqual((r.status_code, r.json()["error"]), (403, "email_unverified"))

    async def test_tokens_that_do_not_verify_are_refused(self):
        bad = {"expired": KEYS.token(exp_in=-120), "wrong project": KEYS.token(aud="other"),
               "unknown key": KEYS.token(kid="k9"), "not a token": "x" * 40}
        async with await self.client() as c:
            for label, tok in bad.items():
                r = await c.post("/v1/auth/session", json={"id_token": tok})
                self.assertEqual(r.status_code, 401, label)

    async def test_sessions_expire_and_sign_out_revokes(self):
        from parallx_db import AuthSession, session_scope
        async with await self.client() as c:
            await self.sign_in(c)
            self.assertEqual((await c.get("/v1/me")).status_code, 200)
            self.assertEqual((await c.post("/v1/auth/logout")).status_code, 204)
            self.assertEqual((await c.get("/v1/me")).status_code, 401)
            await self.sign_in(c)
            with session_scope(DSN) as db:
                for s in db.query(AuthSession).all():
                    s.idle_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            self.assertEqual((await c.get("/v1/me")).status_code, 401, "idle too long")

    async def test_another_sites_page_cannot_use_the_cookie(self):
        async with await self.client() as c:
            await self.sign_in(c)
            r = await c.post("/v1/research/access", json={"ticker": "NVDA"},
                             headers={"origin": "https://evil.example"})
            self.assertEqual((r.status_code, r.json()["error"]), (403, "bad_origin"))
            self.assertEqual((await self.usage(c))["free_credits_used"], 0)


class Billing(_Api):
    async def test_a_webhook_that_does_not_verify_changes_nothing(self):
        async with await self.client() as c:
            me = await self.sign_in(c)
            sub = await self.subscribe(c, me["user"]["user_id"])
            r = await self.hook(c, event("customer.subscription.created", sub),
                                secret="whsec_wrong")
            self.assertEqual(r.status_code, 400)
            self.assertEqual((await c.get("/v1/me/subscription")).json()["plan_id"], "free")

    async def test_a_webhook_delivered_twice_is_applied_once(self):
        from parallx_db import session_scope
        from parallx_db.accounts import UsageLedger
        async with await self.client() as c:
            me = await self.sign_in(c)
            sub = await self.subscribe(c, me["user"]["user_id"])
            ev = event("customer.subscription.created", sub, eid="evt_dup")
            first, second = await self.hook(c, ev), await self.hook(c, ev)
            self.assertEqual(second.json().get("duplicate"), True)
            with session_scope(DSN) as db:
                resets = db.query(UsageLedger).filter_by(kind="period_reset").count()
            self.assertEqual(resets, 1)

    async def test_an_older_event_does_not_undo_a_newer_one(self):
        async with await self.client() as c:
            me = await self.sign_in(c)
            uid = me["user"]["user_id"]
            now = int(time.time())
            canceled = await self.subscribe(c, uid, status="canceled")
            await self.hook(c, event("customer.subscription.deleted", canceled, created=now))
            active = await self.subscribe(c, uid, status="active")
            await self.hook(c, event("customer.subscription.updated", active, created=now - 60))
            self.assertEqual((await c.get("/v1/me/subscription")).json()["plan_id"], "free")

    async def test_a_retried_payment_restores_the_plan(self):
        async with await self.client() as c:
            me = await self.sign_in(c)
            uid = me["user"]["user_id"]
            past = int(time.time()) - 40 * 86400
            await self.subscribe(c, uid, status="past_due", start=past, end=past + 30 * 86400)
            await self.hook(c, event("invoice.payment_failed",
                                     {"id": "in_1", "object": "invoice", "subscription": "sub_1"}))
            s = (await c.get("/v1/me/subscription")).json()
            self.assertEqual(s["plan_id"], "free", "past the grace period")
            await self.subscribe(c, uid, status="active")
            await self.hook(c, event("invoice.paid", {"id": "in_2", "object": "invoice",
                                                      "parent": {"subscription_details": {
                                                          "subscription": "sub_1"}}}))
            self.assertEqual((await c.get("/v1/me/subscription")).json()["plan_id"], "investor")

    async def test_a_payment_needing_the_customer_is_flagged(self):
        async with await self.client() as c:
            me = await self.sign_in(c)
            await self.subscribe(c, me["user"]["user_id"], status="past_due")
            await self.hook(c, event("invoice.payment_action_required",
                                     {"id": "in_3", "object": "invoice", "subscription": "sub_1"}))
            self.assertTrue((await c.get("/v1/me/subscription")).json()["requires_action"])

    async def test_checkout_is_refused_where_usd_billing_is_not_offered(self):
        async with await self.client() as c:
            await self.sign_in(c)
            r = await c.post("/v1/billing/checkout",
                             json={"plan_id": "investor", "interval": "year", "country": "IN"})
            self.assertEqual((r.status_code, r.json()["error"]), (409, "market_unavailable"))
            self.assertEqual(self.stripe.calls, [])

    async def test_checkout_with_a_key_returns_the_same_session(self):
        async with await self.client() as c:
            await self.sign_in(c)
            body = {"plan_id": "analyst", "interval": "year", "country": "GB",
                    "idempotency_key": "k1"}
            a = await c.post("/v1/billing/checkout", json=body)
            b = await c.post("/v1/billing/checkout", json=body)
            self.assertEqual(a.json(), b.json())
            self.assertEqual(sum(1 for call in self.stripe.calls if call[0] == "checkout"), 1)

    async def test_cancel_and_the_portal(self):
        async with await self.client() as c:
            me = await self.sign_in(c)
            self.assertEqual((await c.get("/v1/billing/portal")).status_code, 404)
            sub = await self.subscribe(c, me["user"]["user_id"])
            await self.hook(c, event("customer.subscription.created", sub))
            r = await c.post("/v1/billing/cancel")
            self.assertEqual(r.json()["status"], "cancel_requested")
            self.assertEqual(self.stripe.calls[-1], ("cancel", "sub_1"))
            await self.hook(c, event("customer.subscription.updated",
                                     await self.subscribe(c, me["user"]["user_id"], cancel=True),
                                     created=int(time.time()) + 5))
            s = (await c.get("/v1/me/subscription")).json()
            self.assertEqual((s["cancel_at_period_end"], s["plan_id"]), (True, "investor"),
                             "paid until the period ends")
            self.assertEqual((await c.get("/v1/billing/portal")).json()["url"],
                             "https://billing.stripe.test/p/1")

    async def test_billing_not_configured_says_so(self):
        from api.billing import set_billing
        set_billing(None)
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            async with await self.client() as c:
                await self.sign_in(c)
                r = await c.post("/v1/billing/checkout",
                                 json={"plan_id": "investor", "interval": "month",
                                       "country": "US"})
        self.assertEqual((r.status_code, r.json()["error"]), (503, "billing_not_configured"))


class Plans(_Api):
    async def test_two_paid_plans_and_no_feature_claimed_before_it_works(self):
        async with await self.client() as c:
            plans = (await c.get("/v1/plans")).json()
        self.assertEqual([p["id"] for p in plans], ["free", "investor", "analyst"])
        iix, iixa = plans[1], plans[2]
        self.assertEqual((iix["name"], iix["monthly_price"], iix["annual_price"]), ("Iix", 1900, 19000))
        self.assertEqual((iixa["name"], iixa["monthly_price"], iixa["annual_price"]),
                         ("IIx", 3900, 39000))
        self.assertTrue(iixa["recommended"] and not iix["recommended"])
        coming = {f["label"] for p in plans for f in p["features"] if not f["available"]}
        self.assertIn("Management Promise Tracker", coming)
        self.assertIn("Exportable research reports", coming)
        self.assertTrue(iix["purchasable"])


if __name__ == "__main__":
    unittest.main()
