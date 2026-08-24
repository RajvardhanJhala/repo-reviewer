"""Runtime safety limits: how often the bot may act, and how loudly.

These are the *rate* guardrails (the *capability* guardrails live in gh/client.py
as the allowlist, dry-run, and absence of approve/merge). A review bot that can be
triggered by webhooks is a bot an attacker can try to trigger in a loop; bounding
reviews-per-hour caps both accidental storms and deliberate abuse.

MAX_COMMENTS_PER_PR is enforced in the synthesizer; this module owns the
time-window review limiter.
"""
from __future__ import annotations

import threading
import time
from collections import deque

MAX_REVIEWS_PER_HOUR = 30


class RateLimiter:
    """Sliding-window limiter. Thread-safe: the webhook worker is one thread now,
    but the limiter must stay correct if that ever becomes a pool."""

    def __init__(self, max_events: int = MAX_REVIEWS_PER_HOUR, window_s: float = 3600.0) -> None:
        self.max_events = max_events
        self.window_s = window_s
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            cutoff = now - self.window_s
            while self._events and self._events[0] < cutoff:
                self._events.popleft()
            if len(self._events) >= self.max_events:
                return False
            self._events.append(now)
            return True

    def remaining(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        with self._lock:
            cutoff = now - self.window_s
            while self._events and self._events[0] < cutoff:
                self._events.popleft()
            return self.max_events - len(self._events)
