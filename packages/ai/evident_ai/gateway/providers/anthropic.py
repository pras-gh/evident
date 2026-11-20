"""Anthropic's Messages API, over plain HTTP.

    POST {base}/v1/messages      x-api-key: …   anthropic-version: 2023-06-01

Plain HTTP rather than the SDK so all three adapters share one transport, one
timeout model and one way to be tested, and so the router — not a client
library — owns retries. `anthropic-version` is what the official SDK sends
(read from anthropic 1.8.0); `anthropic_version` in config overrides it.

Structured output is `output_config.format` (no beta header). The system prompt
is marked cacheable; usage reports cache reads and writes separately, and
`input_tokens` excludes both, so the total input is the sum of the three.
A 429 without `retry-after`, or with `enforced_spend_limit_reached`, is a spend
cap: waiting will not help, so it is quota exhaustion.
"""
from __future__ import annotations

from typing import Any, Mapping

import httpx

from ..types import Completion, LLMRequest, Usage
from .base import ErrorKind, ProviderError, deep_merge, kind_for_status, retry_after_seconds

VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, *, base_url: str | None = None,
                 anthropic_version: str = VERSION,
                 transport: httpx.BaseTransport | None = None):
        self._key = api_key
        self._version = anthropic_version
        self._http = httpx.Client(base_url=base_url or "https://api.anthropic.com",
                                  transport=transport)

    def body(self, model: str, request: LLMRequest, params: Mapping[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_output_tokens,
            "system": [{"type": "text", "text": request.system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": request.user}],
        }
        # No temperature: current models run with adaptive thinking, which
        # constrains sampling parameters. Set it per model in config if wanted.
        if request.json_schema is not None:
            body["output_config"] = {"format": {"type": "json_schema",
                                                "schema": request.json_schema}}
        return deep_merge(body, params)

    def complete(self, model: str, request: LLMRequest, *, timeout: float,
                 params: Mapping[str, Any]) -> Completion:
        if not self._key:
            raise ProviderError(ErrorKind.AUTH, "ANTHROPIC_API_KEY is not set")
        try:
            r = self._http.post("/v1/messages", json=self.body(model, request, params),
                                headers={"x-api-key": self._key,
                                         "anthropic-version": self._version},
                                timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError(ErrorKind.TIMEOUT, str(exc) or "timed out") from None
        except httpx.TransportError as exc:
            raise ProviderError(ErrorKind.CONNECTION, str(exc)) from None
        if r.status_code >= 400:
            raise _error(r)
        try:
            data = r.json()
        except ValueError:
            raise ProviderError(ErrorKind.INVALID_RESPONSE, "response was not JSON") from None
        u = data.get("usage") or {}
        read = int(u.get("cache_read_input_tokens") or 0)
        write = int(u.get("cache_creation_input_tokens") or 0)
        usage = Usage(input_tokens=int(u.get("input_tokens") or 0) + read + write,
                      output_tokens=int(u.get("output_tokens") or 0),
                      cached_tokens=read, cache_write_tokens=write)
        text = "".join(b.get("text", "") for b in data.get("content") or []
                       if b.get("type") == "text")
        finish = data.get("stop_reason")
        stop = ("complete" if finish in ("end_turn", "stop_sequence") else
                "max_tokens" if finish == "max_tokens" else
                "refusal" if finish == "refusal" else "other")
        return Completion(text=text, stop=stop, usage=usage, raw_stop=finish,
                          provider_request_id=r.headers.get("request-id") or data.get("id"))


def _error(r: httpx.Response) -> ProviderError:
    try:
        body = r.json()
    except ValueError:
        body = {}
    err = body.get("error") or {}
    message = err.get("message") or r.text[:300] or r.reason_phrase
    kind = kind_for_status(r.status_code)
    retry_after = retry_after_seconds(r.headers)
    code = ((err.get("details") or {}).get("error_code") or "")
    if r.status_code == 429 and (code == "enforced_spend_limit_reached" or retry_after is None):
        kind = ErrorKind.QUOTA_EXHAUSTED
    if r.status_code == 400 and message.startswith("You have reached your specified API usage limits"):
        kind = ErrorKind.QUOTA_EXHAUSTED
    return ProviderError(kind, f"{err.get('type', r.status_code)}: {message}",
                         status=r.status_code, retry_after=retry_after)
