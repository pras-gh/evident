"""Groq, over its OpenAI-compatible chat completions endpoint.

    POST {base}/chat/completions      Authorization: Bearer …

Structured output is `response_format: json_schema` with `strict: false`:
strict mode requires every property to be listed as required, which our
schemas (optional fields) do not do, and the caller validates the result
anyway. A 429 gives `retry-after` in seconds; a wait longer than the router's
`max_retry_wait_s` is a daily quota in practice, and the router falls back.
"""
from __future__ import annotations

from typing import Any, Mapping

import httpx

from ..types import Completion, LLMRequest, Usage
from .base import ErrorKind, ProviderError, deep_merge, kind_for_status, retry_after_seconds


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str | None, *, base_url: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        self._key = api_key
        self._http = httpx.Client(base_url=base_url or "https://api.groq.com/openai/v1",
                                  transport=transport)

    def body(self, model: str, request: LLMRequest, params: Mapping[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": request.system},
                         {"role": "user", "content": request.user}],
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.json_schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "response", "strict": False, "schema": request.json_schema}}
        return deep_merge(body, params)

    def complete(self, model: str, request: LLMRequest, *, timeout: float,
                 params: Mapping[str, Any]) -> Completion:
        if not self._key:
            raise ProviderError(ErrorKind.AUTH, "GROQ_API_KEY is not set")
        try:
            r = self._http.post("/chat/completions",
                                headers={"Authorization": f"Bearer {self._key}"},
                                json=self.body(model, request, params), timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError(ErrorKind.TIMEOUT, str(exc) or "timed out") from None
        except httpx.TransportError as exc:
            raise ProviderError(ErrorKind.CONNECTION, str(exc)) from None
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
            except ValueError:
                err = {}
            message = err.get("message") or r.text[:300] or r.reason_phrase
            raise ProviderError(kind_for_status(r.status_code), f"{r.status_code}: {message}",
                                status=r.status_code, retry_after=retry_after_seconds(r.headers))
        try:
            data = r.json()
            choice = data["choices"][0]
        except (ValueError, KeyError, IndexError):
            raise ProviderError(ErrorKind.INVALID_RESPONSE, "no choices in response") from None
        u = data.get("usage") or {}
        usage = Usage(input_tokens=int(u.get("prompt_tokens", 0)),
                      output_tokens=int(u.get("completion_tokens", 0)),
                      cached_tokens=int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)))
        finish = choice.get("finish_reason")
        stop = ("complete" if finish == "stop" else "max_tokens" if finish == "length"
                else "refusal" if finish == "content_filter" else "other")
        return Completion(text=(choice.get("message") or {}).get("content") or "", stop=stop,
                          usage=usage, raw_stop=finish, provider_request_id=data.get("id"))
