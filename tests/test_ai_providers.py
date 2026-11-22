"""Provider adapters against each provider's wire format, over mocked HTTP.

What each adapter must get right: where the key goes (a header, never the
URL), the request shape the provider expects, usage normalised the same way
for all three, and every failure mapped to a kind the router acts on.

The request and response shapes follow each provider's documentation as read
on 2026-11-21 (docs/ai-gateway.md). No live call is made — none of the three
keys is available — so these prove the adapters do what the docs describe, not
that the docs are right.
"""
from __future__ import annotations

import json
import unittest

import httpx

from parallx_ai.gateway.providers.anthropic import AnthropicProvider
from parallx_ai.gateway.providers.base import ErrorKind, ProviderError
from parallx_ai.gateway.providers.gemini import GeminiProvider
from parallx_ai.gateway.providers.groq import GroqProvider
from parallx_ai.gateway.types import LLMRequest, TaskType

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}}
REQ = LLMRequest(task=TaskType.FILING_SUMMARIZATION, system="Be precise.", user="Summarise.",
                 max_output_tokens=900, json_schema=SCHEMA, temperature=0.0)


class Recorder:
    """A mock transport that records the request and plays one response."""

    def __init__(self, status=200, body=None, headers=None, raise_=None):
        self.status, self.body, self.headers, self.raise_ = status, body or {}, headers or {}, raise_
        self.request: httpx.Request | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.request = request
        if self.raise_:
            raise self.raise_
        return httpx.Response(self.status, json=self.body, headers=self.headers)

    @property
    def json(self):
        return json.loads(self.request.content)


def gemini(rec, key="AIza-test-key-0000000000000"):
    return GeminiProvider(key, transport=httpx.MockTransport(rec))


def groq(rec, key="gsk_testkey00000000"):
    return GroqProvider(key, transport=httpx.MockTransport(rec))


def claude(rec, key="sk-ant-test-0000000000"):
    return AnthropicProvider(key, transport=httpx.MockTransport(rec))


def call(provider, model="m", params=None):
    return provider.complete(model, REQ, timeout=5, params=params or {})


class Gemini(unittest.TestCase):
    OK = {"candidates": [{"content": {"parts": [{"text": "thinking…", "thought": True},
                                                {"text": '{"summary":"ok"}'}]},
                          "finishReason": "STOP"}],
          "usageMetadata": {"promptTokenCount": 1200, "cachedContentTokenCount": 1000,
                            "candidatesTokenCount": 80, "thoughtsTokenCount": 40},
          "responseId": "resp-1"}

    def test_request_shape_and_key_in_a_header_not_the_url(self):
        rec = Recorder(body=self.OK)
        call(gemini(rec), "gemini-3.5-flash-lite")
        self.assertEqual(rec.request.url.path, "/v1beta/models/gemini-3.5-flash-lite:generateContent")
        self.assertEqual(rec.request.headers["x-goog-api-key"], "AIza-test-key-0000000000000")
        self.assertNotIn("key=", str(rec.request.url))
        body = rec.json
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "Be precise."}]})
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "Summarise."}]}])
        self.assertEqual(body["generationConfig"], {
            "maxOutputTokens": 900, "temperature": 0.0, "responseMimeType": "application/json",
            "responseJsonSchema": SCHEMA})

    def test_per_model_params_are_merged_in(self):
        rec = Recorder(body=self.OK)
        call(gemini(rec), params={"generationConfig": {"thinkingConfig": {"thinkingLevel": "LOW"}}})
        cfg = rec.json["generationConfig"]
        self.assertEqual((cfg["thinkingConfig"], cfg["maxOutputTokens"]), ({"thinkingLevel": "LOW"}, 900))

    def test_usage_counts_thinking_as_output_and_skips_thought_text(self):
        c = call(gemini(Recorder(body=self.OK)))
        self.assertEqual(c.text, '{"summary":"ok"}')
        self.assertEqual((c.usage.input_tokens, c.usage.cached_tokens, c.usage.output_tokens),
                         (1200, 1000, 120))
        self.assertEqual((c.stop, c.provider_request_id), ("complete", "resp-1"))

    def test_finish_reasons(self):
        for finish, stop in (("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal"),
                             ("RECITATION", "refusal"), ("MALFORMED_RESPONSE", "other")):
            body = json.loads(json.dumps(self.OK))
            body["candidates"][0]["finishReason"] = finish
            self.assertEqual(call(gemini(Recorder(body=body))).stop, stop, finish)
        blocked = {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 9}}
        c = call(gemini(Recorder(body=blocked)))
        self.assertEqual((c.stop, c.raw_stop, c.usage.input_tokens), ("refusal", "SAFETY", 9))

    def error(self, status, err, headers=None):
        with self.assertRaises(ProviderError) as cm:
            call(gemini(Recorder(status=status, body={"error": err}, headers=headers)))
        return cm.exception

    def test_a_per_minute_limit_is_rate_limited_with_its_retry_delay(self):
        e = self.error(429, {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "slow down",
                             "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                          "retryDelay": "41s"}]})
        self.assertEqual((e.kind, e.retry_after), (ErrorKind.RATE_LIMITED, 41.0))

    def test_a_per_day_quota_is_quota_exhausted(self):
        e = self.error(429, {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                             "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                          "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}]})
        self.assertEqual(e.kind, ErrorKind.QUOTA_EXHAUSTED)

    def test_status_codes(self):
        for status, kind in ((503, ErrorKind.OVERLOADED), (500, ErrorKind.SERVER),
                             (401, ErrorKind.AUTH), (403, ErrorKind.AUTH),
                             (404, ErrorKind.NOT_FOUND), (400, ErrorKind.BAD_REQUEST)):
            self.assertEqual(self.error(status, {"message": "x"}).kind, kind, status)

    def test_network_failures(self):
        rec = Recorder(raise_=httpx.ReadTimeout("read timed out"))
        with self.assertRaises(ProviderError) as cm:
            call(gemini(rec))
        self.assertEqual(cm.exception.kind, ErrorKind.TIMEOUT)
        with self.assertRaises(ProviderError) as cm:
            call(gemini(Recorder(raise_=httpx.ConnectError("refused"))))
        self.assertEqual(cm.exception.kind, ErrorKind.CONNECTION)


class Groq(unittest.TestCase):
    OK = {"id": "chatcmpl-1", "choices": [{"message": {"content": '{"label":"risk"}'},
                                           "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 500, "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 256}}}

    def test_request_shape(self):
        rec = Recorder(body=self.OK)
        call(groq(rec), "openai/gpt-oss-20b")
        self.assertEqual(rec.request.url.path, "/openai/v1/chat/completions")
        self.assertEqual(rec.request.headers["authorization"], "Bearer gsk_testkey00000000")
        body = rec.json
        self.assertEqual(body["model"], "openai/gpt-oss-20b")
        self.assertEqual(body["messages"], [{"role": "system", "content": "Be precise."},
                                            {"role": "user", "content": "Summarise."}])
        self.assertEqual(body["max_completion_tokens"], 900)
        self.assertEqual(body["response_format"], {"type": "json_schema", "json_schema": {
            "name": "response", "strict": False, "schema": SCHEMA}})

    def test_usage_and_finish(self):
        c = call(groq(Recorder(body=self.OK)))
        self.assertEqual((c.text, c.stop, c.usage.input_tokens, c.usage.output_tokens,
                          c.usage.cached_tokens), ('{"label":"risk"}', "complete", 500, 20, 256))
        body = json.loads(json.dumps(self.OK))
        body["choices"][0]["finish_reason"] = "length"
        self.assertEqual(call(groq(Recorder(body=body))).stop, "max_tokens")

    def test_errors(self):
        for status, headers, kind, wait in ((429, {"retry-after": "12"}, ErrorKind.RATE_LIMITED, 12.0),
                                            (498, {}, ErrorKind.OVERLOADED, None),
                                            (413, {}, ErrorKind.TOO_LARGE, None),
                                            (502, {}, ErrorKind.SERVER, None)):
            with self.subTest(status), self.assertRaises(ProviderError) as cm:
                call(groq(Recorder(status=status, body={"error": {"message": "x"}}, headers=headers)))
            self.assertEqual((cm.exception.kind, cm.exception.retry_after), (kind, wait))

    def test_a_200_without_choices_is_an_invalid_response(self):
        with self.assertRaises(ProviderError) as cm:
            call(groq(Recorder(body={"usage": {}})))
        self.assertEqual(cm.exception.kind, ErrorKind.INVALID_RESPONSE)


class Anthropic(unittest.TestCase):
    OK = {"id": "msg_1", "content": [{"type": "text", "text": '{"summary":"ok"}'}],
          "stop_reason": "end_turn",
          "usage": {"input_tokens": 300, "output_tokens": 90, "cache_read_input_tokens": 2000,
                    "cache_creation_input_tokens": 500}}

    def test_request_shape(self):
        rec = Recorder(body=self.OK, headers={"request-id": "req_42"})
        c = call(claude(rec), "claude-sonnet-5-5")
        self.assertEqual(rec.request.url.path, "/v1/messages")
        self.assertEqual((rec.request.headers["x-api-key"], rec.request.headers["anthropic-version"]),
                         ("sk-ant-test-0000000000", "2023-06-01"))
        body = rec.json
        self.assertEqual(body["system"], [{"type": "text", "text": "Be precise.",
                                           "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(body["output_config"], {"format": {"type": "json_schema", "schema": SCHEMA}})
        self.assertEqual((body["max_tokens"], "temperature" in body), (900, False))
        self.assertEqual(c.provider_request_id, "req_42")

    def test_usage_totals_input_across_cache_reads_and_writes(self):
        c = call(claude(Recorder(body=self.OK)))
        self.assertEqual((c.usage.input_tokens, c.usage.cached_tokens, c.usage.cache_write_tokens,
                          c.usage.output_tokens), (2800, 2000, 500, 90))

    def test_stop_reasons(self):
        for finish, stop in (("max_tokens", "max_tokens"), ("refusal", "refusal"),
                             ("stop_sequence", "complete"), ("pause_turn", "other")):
            body = dict(self.OK, stop_reason=finish)
            self.assertEqual(call(claude(Recorder(body=body))).stop, stop)

    def error(self, status, err, headers=None):
        with self.assertRaises(ProviderError) as cm:
            call(claude(Recorder(status=status, body={"type": "error", "error": err},
                                 headers=headers)))
        return cm.exception

    def test_a_rate_limit_with_retry_after_is_retryable(self):
        e = self.error(429, {"type": "rate_limit_error", "message": "slow"}, {"retry-after": "3"})
        self.assertEqual((e.kind, e.retry_after), (ErrorKind.RATE_LIMITED, 3.0))

    def test_a_spend_cap_is_quota_exhausted(self):
        self.assertEqual(self.error(429, {"type": "rate_limit_error", "message": "cap"}).kind,
                         ErrorKind.QUOTA_EXHAUSTED)
        self.assertEqual(self.error(429, {"type": "rate_limit_error", "message": "cap",
                                          "details": {"error_code": "enforced_spend_limit_reached"}},
                                    {"retry-after": "5"}).kind, ErrorKind.QUOTA_EXHAUSTED)
        self.assertEqual(self.error(400, {"type": "invalid_request_error", "message":
                                          "You have reached your specified API usage limits."}).kind,
                         ErrorKind.QUOTA_EXHAUSTED)

    def test_overloaded(self):
        self.assertEqual(self.error(529, {"type": "overloaded_error", "message": "busy"}).kind,
                         ErrorKind.OVERLOADED)


class Keys(unittest.TestCase):
    def test_a_missing_key_fails_as_auth_without_a_request(self):
        for make in (gemini, groq, claude):
            rec = Recorder(body={})
            with self.subTest(make.__name__), self.assertRaises(ProviderError) as cm:
                call(make(rec, key=None))
            self.assertEqual(cm.exception.kind, ErrorKind.AUTH)
            self.assertIsNone(rec.request)

    def test_a_key_echoed_in_an_error_is_redacted(self):
        with self.assertRaises(ProviderError) as cm:
            call(groq(Recorder(status=401, body={"error": {"message": "bad key gsk_testkey00000000"}})))
        self.assertNotIn("gsk_testkey00000000", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
