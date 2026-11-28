"""What a production deployment needs, checked as the API starts.

APP_ENV=production turns this on. A missing or unsafe setting stops the API
before it serves anyone, with a sentence per problem: a new version that
can't start never passes its deploy health check, so the old one keeps
serving while the setting is fixed. Settings a launch can do without (billing
before Stripe is set up, AI answers before a provider key) are warnings: the
API starts and says what is off.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping

AI_KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
#: every real embedding provider, and its key; "hashing" is for tests only
EMBEDDING_KEYS = {"voyage": "VOYAGE_API_KEY", "gemini": "GEMINI_API_KEY",
                  "openai": "OPENAI_API_KEY"}


def is_production(env: Mapping[str, str] = os.environ) -> bool:
    return env.get("APP_ENV", "development") == "production"


def problems(env: Mapping[str, str] = os.environ) -> list[str]:
    """Each setting that stops a production API from starting, as a sentence."""
    if not is_production(env):
        return []
    out: list[str] = []

    def need(name: str, why: str) -> None:
        if not env.get(name, "").strip():
            out.append(f"{name} is not set: {why}")

    need("DATABASE_URL", "the API has nowhere to read or write")
    app_url = env.get("APP_URL", "")
    if not app_url.startswith("https://"):
        out.append("APP_URL must be the app's https:// address (it makes the session "
                   "cookie Secure, and Stripe returns readers to it)")
    if env.get("AUTH_PROVIDER", "") != "firebase":
        out.append("AUTH_PROVIDER must be firebase: dev sign-in accepts anyone who names "
                   "an email address")
    need("FIREBASE_PROJECT_ID", "sign-in tokens can't be verified without it")
    origins = [o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip()]
    if not origins:
        out.append("CORS_ORIGINS is not set: it names the web app's origin, which "
                   "cookie-authenticated requests must come from")
    elif any(not o.startswith("https://") for o in origins):
        out.append("CORS_ORIGINS must list https:// origins only, not "
                   + ", ".join(o for o in origins if not o.startswith("https://")))

    filing_store = env.get("FILING_STORE", "")
    if not filing_store.startswith("s3://"):
        out.append("FILING_STORE must be a bucket (s3://bucket/prefix): a container's "
                   "disk is wiped on every deploy, and the filings with it")
    else:
        need("S3_ACCESS_KEY_ID", "the filing store's bucket can't be reached")
        need("S3_SECRET_ACCESS_KEY", "the filing store's bucket can't be reached")

    need("SEC_USER_AGENT", "EDGAR refuses requests that don't name a contact")
    provider = env.get("EMBEDDING_PROVIDER", "").lower()
    if provider not in EMBEDDING_KEYS:
        out.append(f"EMBEDDING_PROVIDER must be {', '.join(EMBEDDING_KEYS)}: search embeds "
                   "every question, and the hashing provider has no meaning")
    else:
        need(EMBEDDING_KEYS[provider], f"EMBEDDING_PROVIDER={provider} needs its key")
    return out


def warnings(env: Mapping[str, str] = os.environ) -> list[str]:
    """What runs switched off, as sentences. Not reasons to stop."""
    if not is_production(env):
        return []
    from .billing import PRICE_ENV
    out = []
    stripe = ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", *PRICE_ENV.values()]
    missing = [name for name in stripe if not env.get(name)]
    if missing:
        out.append("billing is off until these are set: " + ", ".join(missing)
                   + " (choosing a plan fails, and no subscription can activate)")
    if not any(env.get(k) for k in AI_KEYS):
        out.append("AI answers are off: set one of " + ", ".join(AI_KEYS))
    return out


def enforce(env: Mapping[str, str] = os.environ) -> None:
    """Stop the process if production settings are missing; print warnings."""
    for line in warnings(env):
        print(f"production: warning: {line}", file=sys.stderr)
    found = problems(env)
    if found:
        for line in found:
            print(f"production: {line}", file=sys.stderr)
        raise SystemExit(f"production: {len(found)} setting(s) to fix before the API can start")
