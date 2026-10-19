"""Process-local rate limiting; counts are not shared between workers or restarts."""

import time
from collections import defaultdict


class RateLimiter:
    """Allow at most ``max_per_minute`` calls per key within a rolling 60-second window."""
    def __init__(self, max_per_minute: int = 30):
        self._counts: dict = defaultdict(list)
        self._max = max_per_minute
    def check(self, key: str) -> bool:
        now = time.monotonic()
        calls = [t for t in self._counts[key] if now - t < 60]
        calls.append(now)
        self._counts[key] = calls
        return len(calls) <= self._max
