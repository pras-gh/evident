"""A Messages-API-shaped client that sends everything through the gateway.

Entity extraction was written against `client.messages.create(**params)`, and
its tests and the demo's keyword matcher inject clients of that shape. Rather
than rewrite working code, its default client is this facade: same call, same
response shape, but routed, limited, logged and costed like every other AI
request — and to whichever provider the task's route names.

Only what extraction uses is translated: a system prompt, one user message,
`max_tokens`, and `output_config.format`'s JSON Schema. A `CacheSpec` given at
construction is attached to every request, so a filing's chunks are cached.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .types import AIContext, CacheSpec, LLMRequest, TaskType

_STOP = {"complete": "end_turn", "max_tokens": "max_tokens", "refusal": "refusal",
         "other": "other"}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n\n".join(b.get("text", "") for b in content if b.get("type") == "text")


class GatewayMessages:
    def __init__(self, task: TaskType, ctx: AIContext, gateway: Any | None = None,
                 cache: CacheSpec | None = None):
        self.task = task
        self.ctx = ctx
        self.cache = cache
        self._gateway = gateway

    @property
    def messages(self) -> "GatewayMessages":
        return self

    def create(self, **params: Any) -> SimpleNamespace:
        from . import gateway as default_gateway
        schema = ((params.get("output_config") or {}).get("format") or {}).get("schema")
        request = LLMRequest(
            task=self.task, system=_text(params.get("system") or ""),
            user=_text(params["messages"][-1]["content"]),
            max_output_tokens=params["max_tokens"], json_schema=schema, temperature=0.0,
            cache=self.cache)
        r = (self._gateway or default_gateway()).complete(request, self.ctx)
        u = r.usage
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=r.text)],
            stop_reason=_STOP[r.stop], stop_details=None,
            usage=SimpleNamespace(
                input_tokens=max(0, u.input_tokens - u.cached_tokens - u.cache_write_tokens),
                output_tokens=u.output_tokens, cache_read_input_tokens=u.cached_tokens,
                cache_creation_input_tokens=u.cache_write_tokens),
            provider=r.provider, model=r.model, request_id=r.request_id, cost_usd=r.cost_usd,
            fallback=r.fallback)
