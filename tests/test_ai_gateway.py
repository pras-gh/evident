"""The AI gateway's decisions, without a network or a database.

Providers are scripted (a list of outcomes per provider) and the store is in
memory, so each test states exactly what the providers do and checks what the
gateway did about it: which provider it called, how often, how long it waited,
what it logged, what it refused.
"""
from __future__ import annotations

import io
import json
import logging
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from evident_ai.gateway import (AIContext, AllProvidersFailed, CacheSpec, ConfigError,
                                Gateway, LLMRequest, MemoryStore, TaskType,
                                UnknownOrganization, Usage, UsageLimitExceeded, load)
from evident_ai.gateway import logs as gw_logs
from evident_ai.gateway.config import Target, from_dict
from evident_ai.gateway.providers.base import ErrorKind, ProviderError
from evident_ai.gateway.types import Completion

ORG = uuid.uuid4()
OTHER_ORG = uuid.uuid4()
USER = uuid.uuid4()
NOW = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)

RAW = {
    "gateway": {"timeout_s": 30, "max_attempts": 3, "backoff_base_s": 1.0, "backoff_max_s": 8.0,
                "max_retry_wait_s": 20.0, "breaker_failures": 3, "breaker_cooldown_s": 60.0},
    "routes": {
        "filing_summarization": ["gemini:flash", "groq:oss"],
        "financial_extraction": ["gemini:flash", "groq:oss"],
        "classification": ["groq:oss", "gemini:flash"],
        "complex_financial_reasoning": ["anthropic:sonnet", "gemini:flash"],
        "research_synthesis": ["gemini:flash", "anthropic:sonnet"],
    },
    "limits": {"daily_requests_per_user": 5, "monthly_tokens": 1_000_000,
               "monthly_spend_usd": 10, "max_input_tokens": 50_000, "max_output_tokens": 4_000},
    "prices": [
        {"model": "gemini:flash", "input": 0.30, "output": 2.50, "cached_input": 0.03},
        {"model": "groq:oss", "input": 0.15, "output": 0.60, "cached_input": 0.075},
        {"model": "anthropic:sonnet", "input": 2.0, "output": 10.0, "cached_input": 0.10,
         "cache_write": 2.50},
    ],
}


def config(**overrides):
    raw = json.loads(json.dumps(RAW))
    for section, values in overrides.items():
        raw.setdefault(section, {}).update(values)
    return from_dict(raw, today=date(2026, 11, 21))


def ok(text="{}", inp=1000, out=200, stop="complete", cached=0):
    return Completion(text=text, stop=stop, usage=Usage(input_tokens=inp, output_tokens=out,
                                                         cached_tokens=cached))


def fail(kind, retry_after=None, status=None):
    return ProviderError(kind, f"{kind.value} happened", status=status, retry_after=retry_after)


class Script:
    """A provider that plays back outcomes in order."""

    def __init__(self, name, *outcomes):
        self.name = name
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def complete(self, model, request, *, timeout, params):
        self.calls.append(model)
        if not self.outcomes:
            raise AssertionError(f"{self.name} called more often than scripted")
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def gw(cfg=None, store=None, **scripts):
    store = store or MemoryStore(orgs={ORG: {}, OTHER_ORG: {}})
    providers = {name: scripts.get(name, Script(name)) for name in ("anthropic", "gemini", "groq")}
    clock = Clock()
    g = Gateway(cfg or config(), providers, store, now=lambda: NOW, monotonic=clock.monotonic,
                sleep=clock.sleep, rng=lambda: 1.0)
    return g, store, providers, clock


def req(task=TaskType.FILING_SUMMARIZATION, user="Summarise this filing.", max_out=1000, cache=None):
    return LLMRequest(task=task, system="You are careful.", user=user, max_output_tokens=max_out,
                      cache=cache)


CTX = AIContext(organization_id=ORG, user_id=USER, source="test")


# ====================================================================== config

class Config(unittest.TestCase):
    def test_the_shipped_config_loads_and_every_routed_model_is_priced(self):
        c = load(today=date(2026, 11, 21))
        for task in TaskType:
            for t in c.route(task):
                self.assertGreater(c.price(t, date(2026, 11, 21)).output, 0, t.key)

    def test_one_environment_variable_moves_a_task_from_gemini_to_claude(self):
        """The success criterion: a configuration change, no code change."""
        c = from_dict(RAW, env={"AI_ROUTE_FILING_SUMMARIZATION": "anthropic:sonnet,gemini:flash"},
                      today=date(2026, 11, 21))
        self.assertEqual([t.key for t in c.route(TaskType.FILING_SUMMARIZATION)],
                         ["anthropic:sonnet", "gemini:flash"])
        g, store, p, _ = gw(cfg=c, anthropic=Script("anthropic", ok()))
        r = g.complete(req(), CTX)
        self.assertEqual((r.provider, r.model), ("anthropic", "sonnet"))
        self.assertEqual(p["gemini"].calls, [])

    def test_a_routed_model_without_a_price_is_refused_at_load(self):
        with self.assertRaisesRegex(ConfigError, "no price for gemini:new-model"):
            from_dict(RAW, env={"AI_ROUTE_CLASSIFICATION": "gemini:new-model"},
                      today=date(2026, 11, 21))

    def test_unknown_providers_and_tasks_are_refused(self):
        with self.assertRaisesRegex(ConfigError, "unknown provider 'openai'"):
            from_dict(RAW, env={"AI_ROUTE_CLASSIFICATION": "openai:gpt"}, today=date(2026, 11, 21))
        bad = json.loads(json.dumps(RAW))
        bad["routes"]["poetry"] = ["gemini:flash"]
        with self.assertRaisesRegex(ConfigError, "unknown task"):
            from_dict(bad, today=date(2026, 11, 21))

    def test_dated_prices_take_effect_on_their_day(self):
        raw = json.loads(json.dumps(RAW))
        raw["prices"] = [p for p in raw["prices"] if p["model"] != "gemini:flash"] + [
            {"model": "gemini:flash", "until": "2026-12-31", "input": 0.75, "output": 3.75},
            {"model": "gemini:flash", "from": "2027-01-01", "input": 1.50, "output": 7.50}]
        c = from_dict(raw, today=date(2026, 11, 21))
        flash = Target("gemini", "flash")
        self.assertEqual(c.price(flash, date(2026, 12, 31)).input, Decimal("0.75"))
        self.assertEqual(c.price(flash, date(2027, 1, 1)).input, Decimal("1.50"))

    def test_limits_can_be_overridden_by_environment_and_by_organization(self):
        c = from_dict(RAW, env={"AI_LIMIT_MONTHLY_SPEND_USD": "25"}, today=date(2026, 11, 21))
        self.assertEqual(c.limits.monthly_spend_usd, Decimal("25"))
        self.assertEqual(c.limits.override(daily_requests_per_user=50, monthly_tokens=None),
                         c.limits.__class__(50, 1_000_000, Decimal("25"), 50_000, 4_000))


class Cost(unittest.TestCase):
    c = config()

    def cost(self, key, **usage):
        return self.c.price(Target.parse(key)).cost(Usage(**usage))

    def test_input_and_output_at_their_rates(self):
        # 1M in at $0.30 + 100k out at $2.50
        self.assertEqual(self.cost("gemini:flash", input_tokens=1_000_000, output_tokens=100_000),
                         Decimal("0.550000"))

    def test_cached_input_at_the_cache_rate(self):
        # 800k of 1M input from cache: 200k × 0.30 + 800k × 0.03
        self.assertEqual(self.cost("gemini:flash", input_tokens=1_000_000, cached_tokens=800_000),
                         Decimal("0.084000"))

    def test_anthropic_cache_writes_at_the_write_rate(self):
        # 100k plain × $2 + 50k write × $2.50 + 50k read × $0.10
        self.assertEqual(self.cost("anthropic:sonnet", input_tokens=200_000, cached_tokens=50_000,
                                   cache_write_tokens=50_000), Decimal("0.330000"))

    def test_long_prompts_pay_the_long_context_rate(self):
        c = load(today=date(2026, 11, 21))
        haiku = c.price(Target.parse("anthropic:claude-haiku-5-5"))
        self.assertEqual(haiku.cost(Usage(input_tokens=100_000)), Decimal("0.010000"))
        # one token over the threshold and the whole prompt is at $0.50
        self.assertEqual(haiku.cost(Usage(input_tokens=100_002)), Decimal("0.050001"))

    def test_worst_case_is_every_token_uncached_and_every_output_token_used(self):
        p = self.c.price(Target.parse("anthropic:sonnet"))
        self.assertEqual(p.worst_case(10_000, 4_000), Decimal("0.060000"))


# ===================================================================== routing

class Routing(unittest.TestCase):
    def test_the_primary_answers_and_one_success_row_is_logged_with_its_cost(self):
        g, store, p, _ = gw(gemini=Script("gemini", ok(inp=10_000, out=1_000)))
        r = g.complete(req(), CTX)
        self.assertEqual((r.provider, r.model, r.fallback, r.cached), ("gemini", "flash", False, False))
        self.assertEqual(r.cost_usd, Decimal("0.005500"))
        [row] = store.rows
        self.assertEqual((row.status, row.provider, row.model, row.task_type, row.input_tokens,
                          row.output_tokens, row.estimated_cost_usd, row.user_id, row.organization_id),
                         ("success", "gemini", "flash", "filing_summarization", 10_000, 1_000,
                          Decimal("0.005500"), USER, ORG))
        self.assertEqual(row.request_id, r.request_id)

    def test_a_retryable_failure_is_retried_with_backoff(self):
        g, store, p, clock = gw(gemini=Script("gemini", fail(ErrorKind.OVERLOADED),
                                              fail(ErrorKind.SERVER), ok()))
        r = g.complete(req(), CTX)
        self.assertEqual(p["gemini"].calls, ["flash"] * 3)
        self.assertEqual(clock.slept, [1.0, 2.0])         # base × 2^(n-1), jitter at its maximum
        self.assertEqual([row.status for row in store.rows], ["failed", "failed", "success"])
        self.assertFalse(r.fallback, "a retry on the same provider is not a fallback")
        self.assertEqual(len({row.request_id for row in store.rows}), 1)

    def test_retry_after_is_honoured(self):
        g, _, _, clock = gw(gemini=Script("gemini", fail(ErrorKind.RATE_LIMITED, retry_after=7), ok()))
        g.complete(req(), CTX)
        self.assertEqual(clock.slept, [7])

    def test_a_long_retry_after_falls_back_instead_of_waiting(self):
        g, store, p, clock = gw(gemini=Script("gemini", fail(ErrorKind.RATE_LIMITED, retry_after=3600)),
                                groq=Script("groq", ok()))
        r = g.complete(req(), CTX)
        self.assertEqual(clock.slept, [])
        self.assertEqual((r.provider, r.fallback), ("groq", True))
        self.assertEqual([row.status for row in store.rows], ["failed", "fallback"])

    def test_errors_that_waiting_cannot_fix_are_not_retried(self):
        for kind in (ErrorKind.BAD_REQUEST, ErrorKind.AUTH, ErrorKind.NOT_FOUND,
                     ErrorKind.TOO_LARGE, ErrorKind.QUOTA_EXHAUSTED):
            with self.subTest(kind):
                g, _, p, clock = gw(gemini=Script("gemini", fail(kind)), groq=Script("groq", ok()))
                r = g.complete(req(), CTX)
                self.assertEqual((p["gemini"].calls, clock.slept, r.provider), (["flash"], [], "groq"))

    def test_attempts_are_capped_then_the_fallback_answers(self):
        g, store, p, _ = gw(gemini=Script("gemini", *[fail(ErrorKind.SERVER)] * 3),
                            groq=Script("groq", ok()))
        r = g.complete(req(), CTX)
        self.assertEqual(len(p["gemini"].calls), 3)
        self.assertEqual([a.status for a in r.attempts], ["failed", "failed", "failed", "fallback"])
        self.assertTrue(r.fallback)

    def test_when_everything_fails_every_attempt_is_logged_and_the_error_says_why(self):
        g, store, _, _ = gw(gemini=Script("gemini", fail(ErrorKind.AUTH)),
                            groq=Script("groq", fail(ErrorKind.NOT_FOUND)))
        with self.assertRaises(AllProvidersFailed) as cm:
            g.complete(req(), CTX)
        self.assertEqual([row.status for row in store.rows], ["failed", "failed"])
        self.assertIn("auth", str(cm.exception))
        self.assertEqual({row.request_id for row in store.rows}, {cm.exception.request_id})

    def test_refusals_are_returned_not_retried_or_rerouted(self):
        """A second provider would be paid to refuse too, or worse, to answer
        what the first declined."""
        g, _, p, _ = gw(gemini=Script("gemini", ok(text="", stop="refusal")))
        r = g.complete(req(), CTX)
        self.assertEqual((r.stop, p["groq"].calls), ("refusal", []))


class Breaker(unittest.TestCase):
    def test_repeated_failures_open_the_circuit_and_later_requests_skip_the_provider(self):
        g, store, p, clock = gw(cfg=config(gateway={"max_attempts": 1}),
                                gemini=Script("gemini", *[fail(ErrorKind.SERVER)] * 3),
                                groq=Script("groq", ok(), ok(), ok(), ok()))
        for _ in range(3):
            g.complete(req(), CTX)
        self.assertEqual(g.breaker.state("gemini:flash"), "open")
        r = g.complete(req(), CTX)
        self.assertEqual((len(p["gemini"].calls), r.provider, r.fallback), (3, "groq", True))

    def test_after_the_cooldown_one_probe_is_let_through_and_success_closes_it(self):
        g, _, p, clock = gw(cfg=config(gateway={"max_attempts": 1}),
                            gemini=Script("gemini", *[fail(ErrorKind.SERVER)] * 3, ok()),
                            groq=Script("groq", ok(), ok(), ok()))
        for _ in range(3):
            g.complete(req(), CTX)
        clock.t += 61
        self.assertEqual(g.breaker.state("gemini:flash"), "half_open")
        r = g.complete(req(), CTX)
        self.assertEqual((r.provider, g.breaker.state("gemini:flash")), ("gemini", "closed"))

    def test_quota_exhaustion_opens_the_circuit_at_once_for_as_long_as_asked(self):
        g, _, p, clock = gw(gemini=Script("gemini", fail(ErrorKind.QUOTA_EXHAUSTED, retry_after=600)),
                            groq=Script("groq", ok(), ok()))
        g.complete(req(), CTX)
        clock.t += 120                                   # past the 60s cooldown, inside 600s
        g.complete(req(), CTX)
        self.assertEqual(len(p["gemini"].calls), 1)

    def test_bad_requests_do_not_count_against_the_provider(self):
        g, _, _, _ = gw(cfg=config(gateway={"max_attempts": 1}),
                        gemini=Script("gemini", *[fail(ErrorKind.BAD_REQUEST)] * 5),
                        groq=Script("groq", *[ok()] * 5))
        for _ in range(5):
            g.complete(req(), CTX)
        self.assertEqual(g.breaker.state("gemini:flash"), "closed")


# ====================================================================== limits

class Limits(unittest.TestCase):
    def assertBlocked(self, limit, g, store, p, request=None, ctx=CTX):
        with self.assertRaises(UsageLimitExceeded) as cm:
            g.complete(request or req(), ctx)
        self.assertEqual(cm.exception.limit, limit)
        self.assertEqual(sum(len(x.calls) for x in p.values()), 0, "a provider was called")
        self.assertEqual(store.rows[-1].status, "blocked")
        self.assertIn(f"usage_limit:{limit}", store.rows[-1].error_message)
        return cm.exception

    def test_output_larger_than_the_limit_is_refused(self):
        g, store, p, _ = gw()
        e = self.assertBlocked("max_output_tokens", g, store, p, req(max_out=4_001))
        self.assertEqual((e.allowed, e.current), (4_000, 4_001))

    def test_input_larger_than_the_limit_is_refused(self):
        g, store, p, _ = gw()
        self.assertBlocked("max_input_tokens", g, store, p, req(user="x" * 200_004))

    def test_a_users_daily_requests_are_capped_and_the_response_says_when_it_resets(self):
        g, store, p, _ = gw(gemini=Script("gemini", *[ok()] * 5))
        for _ in range(5):
            g.complete(req(), CTX)
        e = self.assertBlocked("daily_requests_per_user", g, store, p_after(p), None)
        self.assertEqual(e.resets_at, "2026-11-22T00:00:00+00:00")
        self.assertEqual(e.to_json()["error"], "usage_limit")

    def test_retries_and_fallbacks_are_one_request_not_several(self):
        # breaker threshold raised, so three failures do not open the circuit
        g, store, p, _ = gw(cfg=config(gateway={"breaker_failures": 10}),
                            gemini=Script("gemini", fail(ErrorKind.SERVER), fail(ErrorKind.SERVER),
                                          fail(ErrorKind.SERVER), *[ok()] * 4),
                            groq=Script("groq", ok()))
        for _ in range(5):
            g.complete(req(), CTX)                       # the first took four attempts
        self.assertBlocked("daily_requests_per_user", g, store, p_after(p))

    def test_background_jobs_have_no_user_and_no_daily_request_limit(self):
        g, _, _, _ = gw(gemini=Script("gemini", *[ok()] * 8))
        system = AIContext(organization_id=ORG, user_id=None, source="worker")
        for _ in range(8):
            g.complete(req(), system)

    def test_monthly_tokens_are_capped_including_what_this_request_could_use(self):
        store = MemoryStore(orgs={ORG: {}})
        store.rows.append(row(tokens=999_500))   # + ~10 input + 1,000 output > 1,000,000
        g, store, p, _ = gw(store=store)
        self.assertBlocked("monthly_tokens", g, store, p)

    def test_spend_is_capped_at_the_requests_worst_case_cost(self):
        store = MemoryStore(orgs={ORG: {}})
        store.rows.append(row(cost="9.998"))
        g, store, p, _ = gw(store=store)            # worst case on flash ≈ $0.0025
        e = self.assertBlocked("monthly_spend_usd", g, store, p)
        self.assertEqual(e.resets_at, "2026-12-01T00:00:00+00:00")

    def test_a_limit_never_switches_to_another_model(self):
        """The primary is unaffordable; the fallback is cheaper and would fit.
        The request is refused, not quietly answered by something else."""
        store = MemoryStore(orgs={ORG: {}})
        store.rows.append(row(cost="9.9999"))
        g, store, p, _ = gw(store=store, cfg=config(routes={
            "filing_summarization": ["anthropic:sonnet", "groq:oss"]}))
        self.assertBlocked("monthly_spend_usd", g, store, p)

    def test_a_fallback_that_would_break_the_budget_is_not_tried(self):
        store = MemoryStore(orgs={ORG: {}})
        store.rows.append(row(cost="9.97"))          # room for flash, not for sonnet
        g, store, p, _ = gw(store=store, cfg=config(routes={
            "research_synthesis": ["gemini:flash", "anthropic:sonnet"]}),
            gemini=Script("gemini", fail(ErrorKind.AUTH)))
        with self.assertRaises(UsageLimitExceeded):
            g.complete(req(task=TaskType.RESEARCH_SYNTHESIS, max_out=4_000), CTX)
        self.assertEqual(p["anthropic"].calls, [])
        self.assertEqual([r.status for r in store.rows[1:]], ["failed", "blocked"])

    def test_organization_overrides_replace_the_defaults(self):
        store = MemoryStore(orgs={ORG: {"max_output_tokens": 500}})
        g, store, p, _ = gw(store=store)
        self.assertBlocked("max_output_tokens", g, store, p, req(max_out=600))

    def test_an_unknown_organization_is_refused_before_anything_is_spent(self):
        g, _, p, _ = gw()
        with self.assertRaises(UnknownOrganization):
            g.complete(req(), AIContext(organization_id=uuid.uuid4(), source="test"))
        self.assertEqual(p["gemini"].calls, [])


def p_after(p):
    """Providers with their history cleared, so `assertBlocked` checks the
    blocked request alone."""
    for x in p.values():
        x.calls.clear()
    return p


def row(tokens=0, cost="0", org=ORG):
    from evident_ai.gateway.store import LogRow
    return LogRow(organization_id=org, user_id=None, provider="gemini", model="flash",
                  task_type="filing_summarization", request_id=uuid.uuid4(), status="success",
                  created_at=NOW - timedelta(days=1), input_tokens=tokens,
                  estimated_cost_usd=Decimal(cost))


# ======================================================================= cache

class Cache(unittest.TestCase):
    spec = CacheSpec(accession="0001045810-26-000021", prompt_version="filing-summary/1", shared=True)

    def test_an_identical_analysis_is_served_from_cache_at_no_cost(self):
        g, store, p, _ = gw(gemini=Script("gemini", ok(text='{"summary":"x"}')))
        first = g.complete(req(cache=self.spec), CTX)
        second = g.complete(req(cache=self.spec), CTX)
        self.assertEqual(len(p["gemini"].calls), 1)
        self.assertEqual((second.cached, second.text, second.cost_usd), (True, first.text, Decimal(0)))
        self.assertEqual([r.status for r in store.rows], ["success", "cached"])
        self.assertEqual(store.rows[-1].estimated_cost_usd, Decimal(0))

    def test_every_key_component_matters(self):
        g, _, p, _ = gw(gemini=Script("gemini", *[ok()] * 4))
        g.complete(req(cache=self.spec), CTX)
        for variant in (req(cache=CacheSpec("0001045810-25-000023", "filing-summary/1", True)),
                        req(cache=CacheSpec(self.spec.accession, "filing-summary/2", True)),
                        req(cache=self.spec, user="A different input.")):
            g.complete(variant, CTX)
        self.assertEqual(len(p["gemini"].calls), 4)

    def test_a_different_model_is_a_different_entry(self):
        g, _, p, _ = gw(gemini=Script("gemini", ok()))
        g.complete(req(cache=self.spec), CTX)
        g2, _, p2, _ = gw(cfg=config(routes={"filing_summarization": ["groq:oss"]}),
                          store=g.store, groq=Script("groq", ok()))
        g2.complete(req(cache=self.spec), CTX)
        self.assertEqual(p2["groq"].calls, ["oss"])

    def test_an_organizations_own_entries_are_not_served_to_another(self):
        private = CacheSpec(self.spec.accession, "filing-summary/1", shared=False)
        g, _, p, _ = gw(gemini=Script("gemini", ok(), ok()))
        g.complete(req(cache=private), CTX)
        g.complete(req(cache=private), AIContext(organization_id=OTHER_ORG, source="test"))
        self.assertEqual(len(p["gemini"].calls), 2)

    def test_shared_entries_are(self):
        g, _, p, _ = gw(gemini=Script("gemini", ok()))
        g.complete(req(cache=self.spec), CTX)
        g.complete(req(cache=self.spec), AIContext(organization_id=OTHER_ORG, source="test"))
        self.assertEqual(len(p["gemini"].calls), 1)

    def test_truncated_answers_are_not_cached(self):
        g, _, p, _ = gw(gemini=Script("gemini", ok(stop="max_tokens"), ok()))
        g.complete(req(cache=self.spec), CTX)
        g.complete(req(cache=self.spec), CTX)
        self.assertEqual(len(p["gemini"].calls), 2)

    def test_a_fallbacks_answer_is_found_when_the_primary_is_back(self):
        g, _, p, _ = gw(gemini=Script("gemini", fail(ErrorKind.AUTH)), groq=Script("groq", ok()))
        g.complete(req(cache=self.spec), CTX)
        r = g.complete(req(cache=self.spec), CTX)
        self.assertEqual((r.cached, r.provider, len(p["gemini"].calls)), (True, "groq", 1))

    def test_expired_entries_are_not_served(self):
        g, store, p, _ = gw(gemini=Script("gemini", ok(), ok()))
        g.complete(req(cache=self.spec), CTX)
        for e in store.cache.values():
            e.expires_at = NOW - timedelta(seconds=1)
        g.complete(req(cache=self.spec), CTX)
        self.assertEqual(len(p["gemini"].calls), 2)


# ==================================================================== security

class Security(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(gw_logs.JsonFormatter())
        gw_logs.log.addHandler(self.handler)
        gw_logs.log.setLevel(logging.INFO)

    def tearDown(self):
        gw_logs.log.removeHandler(self.handler)

    def test_prompts_and_filing_text_are_never_logged_by_default(self):
        secret_text = "CONFIDENTIAL-FILING-PARAGRAPH-7781"
        g, store, _, _ = gw(gemini=Script("gemini", ok()))
        g.complete(req(user=secret_text), CTX)
        self.assertNotIn(secret_text, self.stream.getvalue())
        self.assertNotIn(secret_text, json.dumps([vars_(r) for r in store.rows], default=str))
        events = [json.loads(line) for line in self.stream.getvalue().splitlines()]
        self.assertEqual([e["event"] for e in events], ["ai.request", "ai.complete"])
        self.assertEqual(events[1]["provider"], "gemini")

    def test_with_log_prompts_on_a_redacted_preview_is_logged(self):
        g, _, _, _ = gw(cfg=config(gateway={"log_prompts": True}), gemini=Script("gemini", ok()))
        g.complete(req(user="Preview me. key=sk-ant-api03-SECRETSECRET"), CTX)
        out = self.stream.getvalue()
        self.assertIn("Preview me", out)
        self.assertNotIn("SECRETSECRET", out)

    def test_secrets_in_provider_errors_are_redacted_before_logging_and_storage(self):
        """A provider error that echoes the key back — the raw message goes in,
        and neither the usage row nor the log line may carry it."""
        import os
        key = "AIzaSyD-THIS-IS-A-REAL-LOOKING-KEY-123"
        os.environ["GEMINI_API_KEY"] = key
        try:
            leak = ProviderError(ErrorKind.AUTH, f"denied for {key} at https://x/v1?key={key}")
            g, store, _, _ = gw(gemini=Script("gemini", leak), groq=Script("groq", ok()))
            g.complete(req(), CTX)
            self.assertIn("[REDACTED]", store.rows[0].error_message)
            self.assertNotIn("REAL-LOOKING", store.rows[0].error_message)
            self.assertNotIn("REAL-LOOKING", self.stream.getvalue())
        finally:
            del os.environ["GEMINI_API_KEY"]

    def test_redaction_covers_every_key_format(self):
        from evident_ai.gateway.redact import redact
        for secret in ("sk-ant-api03-abcdefghijkl", "gsk_abcdefghijklmnop",
                       "AIzaSyAbcdefghijklmnopqrstuv", "evk_abcdefghijklmnop",
                       "Bearer abcdefghijklmnop"):
            with self.subTest(secret):
                self.assertNotIn(secret.split()[-1], redact(f"failed with {secret} here"))
        self.assertEqual(redact("x-goog-api-key: abc123456789"), "x-goog-api-key: [REDACTED]")
        self.assertEqual(redact("https://h/v1?key=abc123456789&a=1"), "https://h/v1?key=[REDACTED]&a=1")
        self.assertEqual(len(redact("y" * 5000)), 500)


def vars_(r):
    return {k: getattr(r, k) for k in r.__slots__}


if __name__ == "__main__":
    unittest.main()
