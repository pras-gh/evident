"""Keep secrets out of logs and out of the database.

Provider error messages echo request details back, and some echo the URL or
header the request carried. Anything the gateway logs or stores goes through
`redact` first: known key formats, Authorization headers, `key=` query
parameters, and the literal value of every secret in the environment.
"""
from __future__ import annotations

import os
import re

#: Environment variables whose values are secret, by name.
_SECRET_NAME = re.compile(r"(API_KEY|_TOKEN|SECRET|PASSWORD)$")

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),          # Anthropic
    re.compile(r"gsk_[A-Za-z0-9]{8,}"),                # Groq
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),            # Google
    re.compile(r"\bAQ\.[0-9A-Za-z_\-]{20,}"),           # Google, newer key format
    re.compile(r"evk_[A-Za-z0-9_\-]{8,}"),             # Parallx API keys
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{8,}=*"),
    re.compile(r"(?i)([?&](?:key|api_key|token)=)[^&\s\"']+"),
    re.compile(r"(?i)((?:x-goog-api-key|x-api-key|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
]

MASK = "[REDACTED]"
MAX_LEN = 500


def _env_secrets() -> list[str]:
    return [v for k, v in os.environ.items() if _SECRET_NAME.search(k) and v and len(v) >= 8]


def redact(text: str | None, *, limit: int = MAX_LEN) -> str | None:
    """`text` with secrets masked, cut to `limit` characters."""
    if text is None:
        return None
    out = str(text)
    for secret in _env_secrets():
        out = out.replace(secret, MASK)
    for pattern in _PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + MASK, out)
    return out if len(out) <= limit else out[: limit - 1] + "…"
