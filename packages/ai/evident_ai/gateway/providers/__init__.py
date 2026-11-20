"""Provider adapters. Only the gateway imports these; business logic never does."""
from __future__ import annotations

from typing import Mapping

import httpx

from ..config import GatewayConfig
from .anthropic import AnthropicProvider
from .base import RETRYABLE, ErrorKind, Provider, ProviderError
from .gemini import GeminiProvider
from .groq import GroqProvider


def build(config: GatewayConfig, *,
          transports: Mapping[str, httpx.BaseTransport] | None = None) -> dict[str, Provider]:
    """One adapter per provider, keyed by name, with keys from the environment.

    A provider with no key still gets an adapter: its attempts fail as `auth`
    and the router falls back, which is what a missing key should do.
    """
    t = transports or {}
    p = config.providers
    return {
        "anthropic": AnthropicProvider(
            p["anthropic"].api_key(), base_url=p["anthropic"].base_url,
            anthropic_version=p["anthropic"].extra.get("anthropic_version", "2023-06-01"),
            transport=t.get("anthropic")),
        "gemini": GeminiProvider(
            p["gemini"].api_key(), base_url=p["gemini"].base_url,
            structured_output_field=p["gemini"].extra.get("structured_output_field",
                                                          "responseJsonSchema"),
            transport=t.get("gemini")),
        "groq": GroqProvider(p["groq"].api_key(), base_url=p["groq"].base_url,
                             transport=t.get("groq")),
    }


__all__ = ["AnthropicProvider", "ErrorKind", "GeminiProvider", "GroqProvider", "Provider",
           "ProviderError", "RETRYABLE", "build"]
