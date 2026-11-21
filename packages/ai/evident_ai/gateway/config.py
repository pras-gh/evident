"""The gateway's configuration: routes, prices, limits, reliability settings.

Read from `config/ai.toml` (or `AI_CONFIG_PATH`), then environment overrides:

    AI_ROUTE_FILING_SUMMARIZATION="anthropic:claude-sonnet-5-5,gemini:gemini-3.5-flash-lite"
    AI_LIMIT_MONTHLY_SPEND_USD=100

One of those is the whole of "switch this task from Gemini to Claude".

Validated at load, so a bad change fails at startup rather than on the first
customer request: every task has a route, every route names a known provider,
and every model on a route has a price in effect today — spend that cannot be
priced cannot be capped.
"""
from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .types import ConfigError, TaskType, Usage

PROVIDERS = ("anthropic", "gemini", "groq")
DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "ai.toml"
MILLION = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Target:
    """One step of a route: a provider and a model."""
    provider: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse(cls, spec: str) -> "Target":
        provider, sep, model = spec.strip().partition(":")
        if not sep or not model:
            raise ConfigError(f"route step {spec!r} is not 'provider:model'")
        if provider not in PROVIDERS:
            raise ConfigError(f"route step {spec!r}: unknown provider {provider!r} "
                              f"(known: {', '.join(PROVIDERS)})")
        return cls(provider, model)


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens, for one model over a span of dates."""
    model: str
    input: Decimal
    output: Decimal
    cached_input: Decimal
    cache_write: Decimal
    start: date | None = None
    end: date | None = None
    above_input_tokens: int | None = None
    long_input: Decimal | None = None
    long_output: Decimal | None = None
    long_cached_input: Decimal | None = None
    long_cache_write: Decimal | None = None

    def applies(self, day: date) -> bool:
        return (self.start is None or day >= self.start) and (self.end is None or day <= self.end)

    def rates(self, input_tokens: int) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        long = self.above_input_tokens is not None and input_tokens > self.above_input_tokens
        if long:
            return (self.long_input or self.input, self.long_output or self.output,
                    self.long_cached_input or self.cached_input,
                    self.long_cache_write or self.cache_write)
        return self.input, self.output, self.cached_input, self.cache_write

    def cost(self, usage: Usage) -> Decimal:
        """What a completion cost. Cached and cache-write tokens are priced at
        their own rates; the rest of the input at the base rate."""
        r_in, r_out, r_cached, r_write = self.rates(usage.input_tokens)
        plain = max(0, usage.input_tokens - usage.cached_tokens - usage.cache_write_tokens)
        total = (Decimal(plain) * r_in + Decimal(usage.cached_tokens) * r_cached
                 + Decimal(usage.cache_write_tokens) * r_write
                 + Decimal(usage.output_tokens) * r_out) / MILLION
        return total.quantize(Decimal("0.000001"))

    def worst_case(self, input_tokens: int, max_output_tokens: int) -> Decimal:
        """The most a request can cost: every input token uncached, every
        output token used. What spending limits are checked against."""
        return self.cost(Usage(input_tokens=input_tokens, output_tokens=max_output_tokens))


@dataclass(frozen=True, slots=True)
class Limits:
    daily_requests_per_user: int
    monthly_tokens: int
    monthly_spend_usd: Decimal
    max_input_tokens: int
    max_output_tokens: int

    def override(self, **values: Any) -> "Limits":
        """Organization-level overrides; None keeps the default."""
        fields = {k: v for k, v in values.items() if v is not None}
        if "monthly_spend_usd" in fields:
            fields["monthly_spend_usd"] = Decimal(str(fields["monthly_spend_usd"]))
        return dataclasses.replace(self, **fields)


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    name: str
    api_key_env: str
    base_url: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) or None


@dataclass(frozen=True, slots=True)
class Reliability:
    timeout_s: float = 90.0
    max_attempts: int = 3
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0
    max_retry_wait_s: float = 20.0
    background_max_wait_s: float = 120.0
    breaker_failures: int = 5
    breaker_cooldown_s: float = 60.0


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    routes: Mapping[TaskType, tuple[Target, ...]]
    prices: tuple[Price, ...]
    limits: Limits
    providers: Mapping[str, ProviderSettings]
    reliability: Reliability
    model_params: Mapping[str, Mapping[str, Any]]
    cache_ttl_days: int = 90
    log_prompts: bool = False

    def route(self, task: TaskType) -> tuple[Target, ...]:
        return self.routes[task]

    def price(self, target: Target, day: date | None = None) -> Price:
        day = day or date.today()
        for p in self.prices:
            if p.model == target.key and p.applies(day):
                return p
        raise ConfigError(f"no price for {target.key} on {day.isoformat()}; add one to "
                          "config/ai.toml [[prices]]")

    def params(self, target: Target) -> Mapping[str, Any]:
        return self.model_params.get(target.key, {})


# ---------------------------------------------------------------- loading

def _dec(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _date(v: Any) -> date | None:
    if v is None:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def load(path: str | Path | None = None, *, env: Mapping[str, str] | None = None,
         today: date | None = None) -> GatewayConfig:
    env = os.environ if env is None else env
    path = Path(path or env.get("AI_CONFIG_PATH") or DEFAULT_PATH)
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"AI config not found at {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"AI config {path} is not valid TOML: {exc}") from None
    return from_dict(raw, env=env, today=today)


def from_dict(raw: Mapping[str, Any], *, env: Mapping[str, str] | None = None,
              today: date | None = None) -> GatewayConfig:
    env = {} if env is None else env
    g = raw.get("gateway", {})

    routes_raw = dict(raw.get("routes", {}))
    for task in TaskType:
        override = env.get(f"AI_ROUTE_{task.value.upper()}")
        if override:
            routes_raw[task.value] = [s for s in override.split(",") if s.strip()]
    unknown = set(routes_raw) - {t.value for t in TaskType}
    if unknown:
        raise ConfigError(f"routes for unknown task(s): {', '.join(sorted(unknown))}")
    routes: dict[TaskType, tuple[Target, ...]] = {}
    for task in TaskType:
        steps = routes_raw.get(task.value)
        if not steps:
            raise ConfigError(f"no route for task {task.value!r}")
        routes[task] = tuple(Target.parse(s) for s in steps)

    prices = tuple(
        Price(model=p["model"], input=_dec(p["input"]), output=_dec(p["output"]),
              cached_input=_dec(p.get("cached_input", p["input"])),
              cache_write=_dec(p.get("cache_write", p["input"])),
              start=_date(p.get("from")), end=_date(p.get("until")),
              above_input_tokens=p.get("above_input_tokens"),
              long_input=_dec(p.get("long_input")), long_output=_dec(p.get("long_output")),
              long_cached_input=_dec(p.get("long_cached_input")),
              long_cache_write=_dec(p.get("long_cache_write")))
        for p in raw.get("prices", []))

    lim = dict(raw.get("limits", {}))
    for name in ("daily_requests_per_user", "monthly_tokens", "monthly_spend_usd",
                 "max_input_tokens", "max_output_tokens"):
        if env.get(f"AI_LIMIT_{name.upper()}"):
            lim[name] = env[f"AI_LIMIT_{name.upper()}"]
    try:
        limits = Limits(daily_requests_per_user=int(lim["daily_requests_per_user"]),
                        monthly_tokens=int(lim["monthly_tokens"]),
                        monthly_spend_usd=Decimal(str(lim["monthly_spend_usd"])),
                        max_input_tokens=int(lim["max_input_tokens"]),
                        max_output_tokens=int(lim["max_output_tokens"]))
    except KeyError as exc:
        raise ConfigError(f"[limits] is missing {exc.args[0]}") from None

    providers = {}
    for name in PROVIDERS:
        p = dict(raw.get("providers", {}).get(name, {}))
        providers[name] = ProviderSettings(
            name=name, api_key_env=p.pop("api_key_env", f"{name.upper()}_API_KEY"),
            base_url=env.get(f"AI_{name.upper()}_BASE_URL") or p.pop("base_url", None),
            extra=p)

    rel = Reliability(**{k: g[k] for k in Reliability.__slots__ if k in g})
    config = GatewayConfig(
        routes=routes, prices=prices, limits=limits, providers=providers, reliability=rel,
        model_params={k: v.get("params", {}) for k, v in raw.get("models", {}).items()},
        cache_ttl_days=int(g.get("cache_ttl_days", 90)),
        log_prompts=str(env.get("AI_LOG_PROMPTS", g.get("log_prompts", False))).lower()
        in ("1", "true", "yes"))

    day = today or date.today()
    for task, steps in routes.items():
        for t in steps:
            config.price(t, day)       # raises ConfigError if unpriced
    return config
