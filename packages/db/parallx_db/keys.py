"""API keys: made once, shown once, stored only as a hash.

    plx_<43 url-safe characters>     256 bits from the OS's CSPRNG

Keys made before the rename start `evk_`. They still work: a key is found by
its hash, never by its prefix.

The database keeps the SHA-256 of the whole key and its first 12 characters
(for telling keys apart in a list). A plain hash is enough here — unlike a
password, the key is random and long, so there is nothing to guess.
"""
from __future__ import annotations

import hashlib
import secrets

PREFIX = "plx_"


def new_key() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def display_prefix(key: str) -> str:
    return key[:12]
