"""A circuit breaker per provider and model.

Closed: requests flow. After `threshold` consecutive failures the circuit
opens and requests skip straight to the fallback, instead of each one paying
the full timeout-and-retry cost against a provider that is down. After
`cooldown` one probe is let through (half-open): success closes the circuit,
failure opens it again.

Failures that will not clear by themselves — quota exhausted, bad key, model
not found — open it at once, for at least as long as the provider asked.

In-process state: each API or worker process keeps its own. That is right for
one API instance and a few workers; a fleet would want it shared.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .providers.base import BREAKER, ErrorKind

IMMEDIATE = frozenset({ErrorKind.QUOTA_EXHAUSTED, ErrorKind.AUTH, ErrorKind.NOT_FOUND})


@dataclass(slots=True)
class _State:
    failures: int = 0
    open_until: float = 0.0
    probing: bool = False


class CircuitBreaker:
    def __init__(self, threshold: int, cooldown: float,
                 clock: Callable[[], float] = time.monotonic):
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self._lock = threading.Lock()
        self._states: dict[str, _State] = {}

    def state(self, key: str) -> str:
        s = self._states.get(key)
        if s is None or s.open_until == 0.0:
            return "closed"
        return "open" if self._clock() < s.open_until else "half_open"

    def allow(self, key: str) -> bool:
        with self._lock:
            s = self._states.setdefault(key, _State())
            if s.open_until == 0.0:
                return True
            if self._clock() < s.open_until:
                return False
            if s.probing:            # one probe at a time while half-open
                return False
            s.probing = True
            return True

    def success(self, key: str) -> None:
        with self._lock:
            self._states[key] = _State()

    def failure(self, key: str, kind: ErrorKind, retry_after: float | None = None) -> bool:
        """Record a failure; True if this opened the circuit."""
        if kind not in BREAKER:
            with self._lock:
                s = self._states.setdefault(key, _State())
                s.probing = False
            return False
        with self._lock:
            s = self._states.setdefault(key, _State())
            s.probing = False
            s.failures += 1
            if kind in IMMEDIATE or s.failures >= self.threshold or s.open_until:
                s.open_until = self._clock() + max(self.cooldown, retry_after or 0.0)
                return True
            return False
