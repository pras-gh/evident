"""The AI gateway: one way into every model, with every request accounted for.

    from parallx_ai.gateway import LLMRequest, TaskType, gateway, system_context

    response = gateway().complete(
        LLMRequest(task=TaskType.FILING_SUMMARIZATION, system=..., user=...,
                   max_output_tokens=2000, json_schema=SCHEMA,
                   cache=CacheSpec(accession, "filing-summary/1", shared=True)),
        ctx)

Business logic names a task, never a provider or a model; `config/ai.toml`
routes tasks to providers. See docs/ai-gateway.md.
"""
from __future__ import annotations

import os
import threading
import uuid

from .config import GatewayConfig, Limits, Target, load
from .router import Gateway, estimate_tokens
from .store import MemoryStore, SqlStore, UnknownOrganization
from .types import (AIContext, AllProvidersFailed, CacheSpec, ConfigError, GatewayError,
                    LLMRequest, LLMResponse, TaskType, Usage, UsageLimitExceeded)

_lock = threading.Lock()
_gateway: Gateway | None = None
_internal_org: uuid.UUID | None = None


def gateway() -> Gateway:
    """The process's gateway, built on first use from config/ai.toml, the
    environment's API keys and DATABASE_URL."""
    global _gateway
    with _lock:
        if _gateway is None:
            from . import providers
            config = load()
            _gateway = Gateway(config, providers.build(config), SqlStore(os.environ.get("DATABASE_URL")))
        return _gateway


def set_gateway(instance: Gateway | None) -> None:
    """Replace the process's gateway — tests, or a reload after a config change."""
    global _gateway, _internal_org
    with _lock:
        _gateway = instance
        _internal_org = None


def internal_organization_id(url: str | None = None) -> uuid.UUID:
    """The organization background jobs are attributed to, created if missing
    (migration 0012 creates it; a schema built from the models does not)."""
    global _internal_org
    if _internal_org is not None:
        return _internal_org
    from sqlalchemy import select
    from parallx_db import INTERNAL_ORG_SLUG, Organization, session_scope
    with session_scope(url or os.environ.get("DATABASE_URL")) as db:
        org = db.execute(select(Organization).where(
            Organization.slug == INTERNAL_ORG_SLUG)).scalar_one_or_none()
        if org is None:
            org = Organization(slug=INTERNAL_ORG_SLUG, name="Parallx (internal)", is_internal=True)
            db.add(org)
            db.flush()
        _internal_org = org.id
    return _internal_org


def missing_keys(task: TaskType, config: GatewayConfig | None = None) -> str | None:
    """Why no provider on `task`'s route could answer, or None if one can.

    For tools about to start paid work: a run with no key would log a failure
    per call and store nothing, so it should stop before it starts.
    """
    config = config or load()
    route = config.route(task)
    if any(config.providers[t.provider].api_key() for t in route):
        return None
    names = " or ".join(sorted({config.providers[t.provider].api_key_env for t in route}))
    return (f"no key for any provider on the {task.value} route "
            f"({', '.join(t.key for t in route)}). Set {names}, "
            "or change the route in config/ai.toml.")


def system_context(source: str) -> AIContext:
    """For work no person asked for: ingest, extraction, backfills. Background,
    so it waits out a provider's rate limit rather than failing."""
    return AIContext(organization_id=internal_organization_id(), user_id=None, source=source,
                     background=True)


__all__ = ["AIContext", "AllProvidersFailed", "CacheSpec", "ConfigError", "Gateway",
           "GatewayConfig", "GatewayError", "LLMRequest", "LLMResponse", "Limits",
           "MemoryStore", "SqlStore", "Target", "TaskType", "UnknownOrganization", "Usage",
           "UsageLimitExceeded", "estimate_tokens", "gateway", "internal_organization_id",
           "load", "missing_keys", "set_gateway", "system_context"]
