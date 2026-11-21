"""What every provider adapter promises, and the one error type they raise.

Adapters translate; they do not decide. An adapter turns a gateway request into
its provider's wire format, the reply back into a `Completion`, and any failure
into a `ProviderError` with a `kind`. Whether to retry, wait, fall back or open
the circuit is the router's call, made the same way for every provider.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping, Protocol

from ..redact import redact
from ..types import Completion, LLMRequest


class ErrorKind(StrEnum):
    RATE_LIMITED = "rate_limited"        # 429 with a short wait
    QUOTA_EXHAUSTED = "quota_exhausted"  # daily/spend quota: waiting will not help today
    OVERLOADED = "overloaded"            # 529, 503, Groq 498
    SERVER = "server"                    # other 5xx
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    AUTH = "auth"                        # 401/403, or no key configured
    NOT_FOUND = "not_found"              # 404: usually a retired model ID
    TOO_LARGE = "too_large"              # 413
    BAD_REQUEST = "bad_request"          # 400/422: this request, as sent, will not work
    INVALID_RESPONSE = "invalid_response"  # 200 we could not read


#: Worth trying the same provider again, after a wait.
RETRYABLE = frozenset({ErrorKind.RATE_LIMITED, ErrorKind.OVERLOADED, ErrorKind.SERVER,
                       ErrorKind.TIMEOUT, ErrorKind.CONNECTION})

#: Count towards opening the circuit. A bad request is the request's fault,
#: not the provider's, so it does not.
BREAKER = RETRYABLE | {ErrorKind.QUOTA_EXHAUSTED, ErrorKind.AUTH, ErrorKind.NOT_FOUND,
                       ErrorKind.INVALID_RESPONSE}


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None,
                 retry_after: float | None = None):
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.message = redact(message) or kind.value
        super().__init__(f"{kind.value}: {self.message}")

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


def kind_for_status(status: int) -> ErrorKind:
    if status == 429:
        return ErrorKind.RATE_LIMITED
    if status in (401, 403):
        return ErrorKind.AUTH
    if status == 404:
        return ErrorKind.NOT_FOUND
    if status == 413:
        return ErrorKind.TOO_LARGE
    if status in (503, 529, 498):
        return ErrorKind.OVERLOADED
    if status >= 500:
        return ErrorKind.SERVER
    return ErrorKind.BAD_REQUEST


def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None   # an HTTP date: treat as unknown


class Provider(Protocol):
    name: str

    def complete(self, model: str, request: LLMRequest, *, timeout: float,
                 params: Mapping[str, Any]) -> Completion: ...


def deep_merge(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    """Per-model params from the config, merged into a request body."""
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out
