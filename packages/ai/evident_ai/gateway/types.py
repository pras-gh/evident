"""What callers hand the gateway, and what they get back.

Business logic speaks only in these terms: a task, a prompt, an optional
JSON Schema, a budget for output, and who is asking. It never names a provider
or a model — the routing configuration does.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal


class TaskType(StrEnum):
    """What the request is for. Routing is configured per task."""
    FILING_SUMMARIZATION = "filing_summarization"
    FINANCIAL_EXTRACTION = "financial_extraction"
    CLASSIFICATION = "classification"
    COMPLEX_FINANCIAL_REASONING = "complex_financial_reasoning"
    RESEARCH_SYNTHESIS = "research_synthesis"


#: How a completion ended, the same for every provider.
Stop = Literal["complete", "max_tokens", "refusal", "other"]


@dataclass(frozen=True, slots=True)
class AIContext:
    """Who a request is for. Every request has an organization; a request a
    person made also has their user id. `source` names the code path, for logs.

    `background` marks work no one is waiting on: ingest, extraction,
    backfills. It waits out a provider's rate limit, up to
    `background_max_wait_s`, where a request someone is waiting on falls back
    after `max_retry_wait_s`.
    """
    organization_id: uuid.UUID
    user_id: uuid.UUID | None = None
    source: str = "unknown"
    background: bool = False


@dataclass(frozen=True, slots=True)
class CacheSpec:
    """Opt-in response caching for filing analysis.

    `shared=True` only when the input is public filing text and our own prompt —
    then one organization's result can answer another's identical request.
    Anything carrying a customer's own words must leave it False, which scopes
    the cache entry to their organization.
    """
    accession: str
    prompt_version: str
    shared: bool = False


@dataclass(slots=True)
class LLMRequest:
    task: TaskType
    system: str
    user: str
    max_output_tokens: int
    #: when set, the response is constrained to this schema (each provider's
    #: structured-output mode); the caller still validates it
    json_schema: dict[str, Any] | None = None
    temperature: float | None = 0.0
    cache: CacheSpec | None = None


@dataclass(slots=True)
class Usage:
    """Tokens, normalised across providers.

    `input_tokens` is every input token, cached ones included; `cached_tokens`
    is the part served from the provider's prompt cache; `cache_write_tokens`
    the part written to it (Anthropic bills those at a premium).
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0

    def to_json(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cached_tokens": self.cached_tokens,
                "cache_write_tokens": self.cache_write_tokens}


@dataclass(slots=True)
class Completion:
    """One provider's answer to one attempt."""
    text: str
    stop: Stop
    usage: Usage
    #: the provider's own stop/finish value, for diagnosis
    raw_stop: str | None = None
    provider_request_id: str | None = None


@dataclass(slots=True)
class Attempt:
    provider: str
    model: str
    status: Literal["success", "failed", "fallback"]
    latency_ms: int
    error: str | None = None


@dataclass(slots=True)
class LLMResponse:
    request_id: uuid.UUID
    text: str
    stop: Stop
    provider: str
    model: str
    usage: Usage
    cost_usd: Decimal
    latency_ms: int
    #: answered from the response cache: no provider was called
    cached: bool = False
    #: answered by a provider after the primary failed — never silently
    fallback: bool = False
    attempts: list[Attempt] = field(default_factory=list)


class GatewayError(RuntimeError):
    """Base for everything the gateway refuses or cannot do."""


class UsageLimitExceeded(GatewayError):
    """A configured limit stops this request. No provider was called.

    `limit` is one of: daily_requests_per_user, monthly_tokens,
    monthly_spend_usd, max_input_tokens, max_output_tokens.
    """

    def __init__(self, limit: str, *, allowed: float | int, current: float | int,
                 resets_at: str | None, request_id: uuid.UUID, message: str):
        super().__init__(message)
        self.limit = limit
        self.allowed = allowed
        self.current = current
        self.resets_at = resets_at
        self.request_id = request_id
        self.message = message

    def to_json(self) -> dict[str, Any]:
        return {"error": "usage_limit", "limit": self.limit, "allowed": self.allowed,
                "current": self.current, "resets_at": self.resets_at,
                "request_id": str(self.request_id), "message": self.message}


class AllProvidersFailed(GatewayError):
    """Every provider on the route failed or was unavailable."""

    def __init__(self, request_id: uuid.UUID, attempts: list[Attempt], message: str):
        super().__init__(message)
        self.request_id = request_id
        self.attempts = attempts


class ConfigError(GatewayError):
    """The routing or pricing configuration is unusable."""
