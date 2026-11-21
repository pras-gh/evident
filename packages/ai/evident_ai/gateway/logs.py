"""Structured logs for the gateway: one JSON object per line, secrets redacted.

Events carry identifiers, sizes and timings — request id, task, provider,
model, tokens, cost, status — never the prompt or the filing text. With
`log_prompts` on (config or AI_LOG_PROMPTS=1) a request's first 500 characters
are added, for debugging only.

    from evident_ai.gateway.logs import configure_logging
    configure_logging()          # the API and workers call this at startup
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .redact import redact

log = logging.getLogger("evident.ai")

#: LogRecord attributes that are not event fields
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                out[key] = value
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(out, default=str), limit=20_000)


class RedactingFilter(logging.Filter):
    """Belt and braces for any handler that is not the JSON one."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg, limit=20_000)
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """JSON lines on stderr for the gateway's logger. Idempotent."""
    if any(getattr(h, "_evident_json", False) for h in log.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    handler._evident_json = True  # type: ignore[attr-defined]
    log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False


def event(name: str, level: int = logging.INFO, **fields: Any) -> None:
    log.log(level, name, extra=fields)
