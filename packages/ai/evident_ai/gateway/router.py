"""The gateway: every AI request in the codebase goes through `Gateway.complete`.

    response = gateway().complete(LLMRequest(task=..., system=..., user=..., ...), ctx)

For one request, in order:

1. **Size limits** — input (estimated) and output (as asked) against the
   organization's maximums. Refused before anything is spent.
2. **Cache** — when the request names a filing and prompt version, each model
   on the route is looked up; a hit is returned at no cost.
3. **Quota limits** — the user's requests today, the organization's tokens and
   spend this month. Spend is checked against the request's *worst case* —
   every input token uncached, every output token used — so the cap holds.
4. **Route** — the task's providers in order. For each: skip it if its circuit
   is open; check the spend limit again at its own price (a fallback can cost
   more); try it, retrying what is worth retrying with exponential backoff and
   honouring `retry-after` up to a limit; on failure move to the next.

Every provider attempt is logged — successes, failures, the fallback that
answered — and so is every request answered from cache or refused by a limit.
Nothing is logged of the prompt itself. A limit never routes a request to a
different model: it refuses it, with a response that says which limit and when
it resets.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Mapping

from . import logs
from .breaker import CircuitBreaker
from .config import GatewayConfig, Limits, Target
from .providers.base import ErrorKind, Provider, ProviderError
from .redact import redact
from .store import CacheEntry, LogRow, Store, windows
from .types import (AIContext, AllProvidersFailed, Attempt, CacheSpec, LLMRequest,
                    LLMResponse, Usage, UsageLimitExceeded)


def estimate_tokens(request: LLMRequest) -> int:
    """Input tokens, estimated before sending: ~4 characters a token for
    English prose. Used for limits and worst-case cost only — logs and costs
    use the provider's own count."""
    chars = len(request.system) + len(request.user)
    if request.json_schema is not None:
        chars += len(json.dumps(request.json_schema))
    return math.ceil(chars / 4)


def _sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def input_hash(request: LLMRequest) -> str:
    return _sha({"task": request.task.value, "system": request.system, "user": request.user,
                 "schema": request.json_schema, "max_output_tokens": request.max_output_tokens,
                 "temperature": request.temperature})


def cache_key(spec: CacheSpec, request: LLMRequest, target: Target, ctx: AIContext) -> tuple[str, str, str]:
    """(key, input hash, scope). Shared entries serve every organization;
    others only the one that paid for them."""
    scope = "shared" if spec.shared else str(ctx.organization_id)
    ih = input_hash(request)
    key = _sha({"accession": spec.accession, "task": request.task.value,
                "provider": target.provider, "model": target.model,
                "prompt_version": spec.prompt_version, "input_hash": ih, "scope": scope})
    return key, ih, scope


class Gateway:
    def __init__(self, config: GatewayConfig, providers: Mapping[str, Provider], store: Store, *,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 rng: Callable[[], float] = random.random):
        self.config = config
        self.providers = providers
        self.store = store
        self._now = now
        self._monotonic = monotonic
        self._sleep = sleep
        self._rng = rng
        rel = config.reliability
        self.breaker = CircuitBreaker(rel.breaker_failures, rel.breaker_cooldown_s, monotonic)

    # ------------------------------------------------------------- public
    def limits(self, ctx: AIContext) -> Limits:
        return self.config.limits.override(**self.store.org_overrides(ctx.organization_id))

    def complete(self, request: LLMRequest, ctx: AIContext) -> LLMResponse:
        request_id = uuid.uuid4()
        started = self._monotonic()
        route = self.config.route(request.task)
        primary = route[0]
        est_in = estimate_tokens(request)
        limits = self.limits(ctx)      # also: an unknown organization fails here, closed

        fields = dict(request_id=str(request_id), task=request.task.value,
                      organization_id=str(ctx.organization_id),
                      user_id=str(ctx.user_id) if ctx.user_id else None, source=ctx.source,
                      estimated_input_tokens=est_in, max_output_tokens=request.max_output_tokens)
        if self.config.log_prompts:
            fields["prompt_preview"] = redact(request.user, limit=500)
        logs.event("ai.request", **fields)

        # 1. size
        if request.max_output_tokens > limits.max_output_tokens:
            self._block(ctx, request, primary, request_id, "max_output_tokens",
                        limits.max_output_tokens, request.max_output_tokens, None,
                        f"asked for {request.max_output_tokens} output tokens; the limit is "
                        f"{limits.max_output_tokens}")
        if est_in > limits.max_input_tokens:
            self._block(ctx, request, primary, request_id, "max_input_tokens",
                        limits.max_input_tokens, est_in, None,
                        f"the input is about {est_in} tokens; the limit is "
                        f"{limits.max_input_tokens}")

        # 2. cache
        if request.cache is not None:
            hit = self._cache_lookup(request, route, ctx)
            if hit is not None:
                return self._serve_cached(hit, request, ctx, request_id, started)

        # 3. quotas
        now = self._now()
        counts = self.store.counts(ctx.organization_id, ctx.user_id, now)
        _, next_day, _, next_month = windows(now)
        if ctx.user_id is not None and counts.user_requests_today >= limits.daily_requests_per_user:
            self._block(ctx, request, primary, request_id, "daily_requests_per_user",
                        limits.daily_requests_per_user, counts.user_requests_today, next_day,
                        f"{counts.user_requests_today} AI requests today; the daily limit is "
                        f"{limits.daily_requests_per_user}")
        if counts.org_tokens_month + est_in + request.max_output_tokens > limits.monthly_tokens:
            self._block(ctx, request, primary, request_id, "monthly_tokens",
                        limits.monthly_tokens, counts.org_tokens_month, next_month,
                        f"{counts.org_tokens_month:,} tokens used this month; this request could "
                        f"use {est_in + request.max_output_tokens:,} more and the limit is "
                        f"{limits.monthly_tokens:,}")

        # 4. route
        attempts: list[Attempt] = []
        for index, target in enumerate(route):
            if not self.breaker.allow(target.key):
                logs.event("ai.skipped", request_id=str(request_id), provider=target.provider,
                           model=target.model, reason="circuit_open")
                continue
            worst = self.config.price(target, now.date()).worst_case(est_in, request.max_output_tokens)
            if counts.org_spend_month + worst > limits.monthly_spend_usd:
                self._block(ctx, request, target, request_id, "monthly_spend_usd",
                            float(limits.monthly_spend_usd), float(counts.org_spend_month),
                            next_month,
                            f"${counts.org_spend_month:.2f} spent this month; this request on "
                            f"{target.key} could cost up to ${worst:.4f} and the limit is "
                            f"${limits.monthly_spend_usd:.2f}")
            # any answer not from the primary is a fallback, said so in the
            # response and the log — including when the primary was skipped
            # because its circuit was open
            result = self._try(target, request, ctx, request_id, attempts, fallback=index > 0)
            if result is not None:
                completion, latency = result
                cost = self.config.price(target, now.date()).cost(completion.usage)
                if request.cache is not None and completion.stop == "complete":
                    self._cache_store(request, target, ctx, completion, cost)
                response = LLMResponse(
                    request_id=request_id, text=completion.text, stop=completion.stop,
                    provider=target.provider, model=target.model, usage=completion.usage,
                    cost_usd=cost, latency_ms=self._ms(started),
                    fallback=attempts[-1].status == "fallback", attempts=attempts)
                logs.event("ai.complete", request_id=str(request_id), provider=target.provider,
                           model=target.model, status=attempts[-1].status,
                           input_tokens=completion.usage.input_tokens,
                           output_tokens=completion.usage.output_tokens,
                           cached_tokens=completion.usage.cached_tokens,
                           cost_usd=str(cost), latency_ms=response.latency_ms,
                           stop=completion.stop)
                return response

        message = ("every provider on the route failed or was unavailable: "
                   + "; ".join(f"{a.provider}:{a.model} {a.error}" for a in attempts if a.error)
                   if attempts else "every provider on the route has an open circuit")
        if not attempts:
            # No provider was called, so no attempt logged it; the request
            # still happened and still failed, and someone will look for it.
            self._log(ctx, request, primary, request_id, "failed", self._ms(started),
                      error=message)
        logs.event("ai.failed", level=40, request_id=str(request_id), task=request.task.value,
                   error=message)
        raise AllProvidersFailed(request_id, attempts, message)

    # ------------------------------------------------------------ attempts
    def _try(self, target: Target, request: LLMRequest, ctx: AIContext, request_id: uuid.UUID,
             attempts: list[Attempt], *, fallback: bool):
        """Up to `max_attempts` calls to one provider. Returns (completion,
        latency_ms) or None to move on to the next provider."""
        rel = self.config.reliability
        provider = self.providers[target.provider]
        params = self.config.params(target)
        for n in range(1, rel.max_attempts + 1):
            t0 = self._monotonic()
            try:
                completion = provider.complete(target.model, request, timeout=rel.timeout_s,
                                               params=params)
            except ProviderError as exc:
                latency = self._ms(t0)
                attempts.append(Attempt(target.provider, target.model, "failed", latency, exc.message))
                self._log(ctx, request, target, request_id, "failed", latency, error=exc.message)
                opened = self.breaker.failure(target.key, exc.kind, exc.retry_after)
                logs.event("ai.attempt_failed", level=30, request_id=str(request_id),
                           provider=target.provider, model=target.model, attempt=n,
                           kind=exc.kind.value, status_code=exc.status, error=exc.message,
                           circuit_opened=opened)
                wait = self._wait(exc, n, ctx)
                if wait is None or n == rel.max_attempts or opened:
                    return None
                logs.event("ai.retry", request_id=str(request_id), provider=target.provider,
                           model=target.model, attempt=n, wait_s=round(wait, 2))
                self._sleep(wait)
                continue
            latency = self._ms(t0)
            self.breaker.success(target.key)
            status = "fallback" if fallback else "success"
            cost = self.config.price(target, self._now().date()).cost(completion.usage)
            attempts.append(Attempt(target.provider, target.model, status, latency))
            self._log(ctx, request, target, request_id, status, latency,
                      usage=completion.usage, cost=cost)
            if fallback:
                logs.event("ai.fallback", level=30, request_id=str(request_id),
                           provider=target.provider, model=target.model, task=request.task.value)
            return completion, latency
        return None

    def _wait(self, exc: ProviderError, attempt: int, ctx: AIContext) -> float | None:
        """Seconds to wait before retrying the same provider, or None to fall
        back now. Never waits on an error that waiting cannot fix.

        With someone waiting, never longer than `max_retry_wait_s`: past that
        the next provider is the better bet. Background work waits up to
        `background_max_wait_s`, because a per-minute limit says exactly when
        it lifts (Gemini's free tier asks for up to a minute), and a backfill
        that falls back instead fails every chunk until it does."""
        rel = self.config.reliability
        if not exc.retryable:
            return None
        cap = rel.background_max_wait_s if ctx.background else rel.max_retry_wait_s
        if exc.retry_after is not None:
            return exc.retry_after if exc.retry_after <= cap else None
        backoff = min(rel.backoff_max_s, rel.backoff_base_s * 2 ** (attempt - 1))
        return backoff * (0.5 + self._rng() / 2)       # jittered: 50–100% of the step

    # ------------------------------------------------------------- cache
    def _cache_lookup(self, request: LLMRequest, route, ctx: AIContext) -> CacheEntry | None:
        now = self._now()
        for target in route:
            key, _, _ = cache_key(request.cache, request, target, ctx)
            hit = self.store.cache_get(key, now)
            if hit is not None:
                return hit
        return None

    def _serve_cached(self, hit: CacheEntry, request: LLMRequest, ctx: AIContext,
                      request_id: uuid.UUID, started: float) -> LLMResponse:
        target = Target(hit.provider, hit.model)
        latency = self._ms(started)
        self._log(ctx, request, target, request_id, "cached", latency)
        logs.event("ai.cache_hit", request_id=str(request_id), provider=hit.provider,
                   model=hit.model, accession=hit.accession, task=request.task.value)
        return LLMResponse(request_id=request_id, text=hit.response_text, stop=hit.stop_reason,
                           provider=hit.provider, model=hit.model, usage=Usage(),
                           cost_usd=Decimal(0), latency_ms=latency, cached=True)

    def _cache_store(self, request: LLMRequest, target: Target, ctx: AIContext,
                     completion, cost: Decimal) -> None:
        spec = request.cache
        key, ih, scope = cache_key(spec, request, target, ctx)
        now = self._now()
        try:
            self.store.cache_put(CacheEntry(
                cache_key=key, accession=spec.accession, task_type=request.task.value,
                provider=target.provider, model=target.model, prompt_version=spec.prompt_version,
                input_hash=ih, scope=scope, response_text=completion.text,
                stop_reason=completion.stop, usage=completion.usage.to_json(),
                estimated_cost_usd=cost, created_at=now,
                expires_at=now + timedelta(days=self.config.cache_ttl_days)))
        except Exception as exc:          # a cache that cannot write is not a failed request
            logs.event("ai.cache_write_failed", level=30, error=redact(str(exc)))

    # ----------------------------------------------------------- logging
    def _block(self, ctx: AIContext, request: LLMRequest, target: Target, request_id: uuid.UUID,
               limit: str, allowed, current, resets: datetime | None, message: str) -> None:
        self._log(ctx, request, target, request_id, "blocked", 0, error=f"usage_limit:{limit}: {message}")
        logs.event("ai.blocked", level=30, request_id=str(request_id), limit=limit,
                   organization_id=str(ctx.organization_id),
                   user_id=str(ctx.user_id) if ctx.user_id else None)
        raise UsageLimitExceeded(limit, allowed=allowed, current=current,
                                 resets_at=resets.isoformat() if resets else None,
                                 request_id=request_id, message=message)

    def _log(self, ctx: AIContext, request: LLMRequest, target: Target, request_id: uuid.UUID,
             status: str, latency_ms: int, *, usage: Usage | None = None,
             cost: Decimal = Decimal(0), error: str | None = None) -> None:
        usage = usage or Usage()
        row = LogRow(organization_id=ctx.organization_id, user_id=ctx.user_id,
                     provider=target.provider, model=target.model,
                     task_type=request.task.value, request_id=request_id, status=status,
                     created_at=self._now(), input_tokens=usage.input_tokens,
                     output_tokens=usage.output_tokens, cached_tokens=usage.cached_tokens,
                     estimated_cost_usd=cost, latency_ms=latency_ms,
                     error_message=redact(error))
        try:
            self.store.log(row)
        except Exception as exc:
            # The provider was paid either way. If the row cannot be written,
            # the structured log still has everything it held.
            logs.event("ai.usage_log_failed", level=40, error=redact(str(exc)),
                       **{k: str(v) for k, v in vars_of(row).items()})

    def _ms(self, since: float) -> int:
        return int((self._monotonic() - since) * 1000)


def vars_of(row: LogRow) -> dict:
    return {k: getattr(row, k) for k in LogRow.__slots__}
