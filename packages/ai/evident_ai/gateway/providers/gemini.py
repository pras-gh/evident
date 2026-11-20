"""Google Gemini, over the REST `generateContent` endpoint.

    POST {base}/v1beta/models/{model}:generateContent      x-goog-api-key: …

Usage: `promptTokenCount` already includes `cachedContentTokenCount`; output
is `candidatesTokenCount` plus `thoughtsTokenCount`, because thinking is billed
as output. A 429 `RESOURCE_EXHAUSTED` carries a `RetryInfo.retryDelay` for a
per-minute limit; a per-day quota violation means waiting will not help today,
so it is reported as quota exhaustion and the router falls back.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

import httpx

from ..types import Completion, LLMRequest, Usage
from .base import ErrorKind, ProviderError, deep_merge, kind_for_status, retry_after_seconds

_REFUSAL = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION",
            "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT"}


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str | None, *, base_url: str | None = None,
                 structured_output_field: str = "responseJsonSchema",
                 transport: httpx.BaseTransport | None = None):
        self._key = api_key
        self._field = structured_output_field
        self._http = httpx.Client(base_url=base_url or "https://generativelanguage.googleapis.com",
                                  transport=transport)

    def body(self, request: LLMRequest, params: Mapping[str, Any]) -> dict[str, Any]:
        config: dict[str, Any] = {"maxOutputTokens": request.max_output_tokens}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.json_schema is not None:
            config["responseMimeType"] = "application/json"
            config[self._field] = request.json_schema
        body = {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": [{"text": request.user}]}],
            "generationConfig": config,
        }
        return deep_merge(body, params)

    def complete(self, model: str, request: LLMRequest, *, timeout: float,
                 params: Mapping[str, Any]) -> Completion:
        if not self._key:
            raise ProviderError(ErrorKind.AUTH, "GEMINI_API_KEY is not set")
        try:
            r = self._http.post(f"/v1beta/models/{model}:generateContent",
                                headers={"x-goog-api-key": self._key},
                                json=self.body(request, params), timeout=timeout)
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
        return _completion(data)


def _completion(data: dict[str, Any]) -> Completion:
    meta = data.get("usageMetadata") or {}
    usage = Usage(
        input_tokens=int(meta.get("promptTokenCount", 0)) + int(meta.get("toolUsePromptTokenCount", 0)),
        output_tokens=int(meta.get("candidatesTokenCount", 0)) + int(meta.get("thoughtsTokenCount", 0)),
        cached_tokens=int(meta.get("cachedContentTokenCount", 0)))
    block = (data.get("promptFeedback") or {}).get("blockReason")
    candidates = data.get("candidates") or []
    if block or not candidates:
        return Completion(text="", stop="refusal", usage=usage,
                          raw_stop=block or "NO_CANDIDATES",
                          provider_request_id=data.get("responseId"))
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    # thought summaries, if requested, are not the answer
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    finish = cand.get("finishReason")
    stop = ("complete" if finish == "STOP" else "max_tokens" if finish == "MAX_TOKENS"
            else "refusal" if finish in _REFUSAL else "other")
    return Completion(text=text, stop=stop, usage=usage, raw_stop=finish,
                      provider_request_id=data.get("responseId"))


def _delay(text: str | None) -> float | None:
    m = re.fullmatch(r"([\d.]+)s", text or "")
    return float(m.group(1)) if m else None


def _error(r: httpx.Response) -> ProviderError:
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {}
    message = err.get("message") or r.text[:300] or r.reason_phrase
    kind = kind_for_status(r.status_code)
    retry_after = retry_after_seconds(r.headers)
    for detail in err.get("details") or []:
        dtype = detail.get("@type", "")
        if dtype.endswith("RetryInfo"):
            retry_after = _delay(detail.get("retryDelay")) or retry_after
        if dtype.endswith("QuotaFailure"):
            if any("PerDay" in (v.get("quotaId") or "") for v in detail.get("violations") or []):
                kind = ErrorKind.QUOTA_EXHAUSTED
    if r.status_code == 429 and retry_after is None and "spend" in message.lower():
        kind = ErrorKind.QUOTA_EXHAUSTED
    return ProviderError(kind, f"{err.get('status', r.status_code)}: {message}",
                         status=r.status_code, retry_after=retry_after)
