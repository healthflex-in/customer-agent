"""Per-user in-memory rate limiter (token bucket, 1-minute window)."""
import time
from collections import defaultdict

class RateLimiter:
    def __init__(self, max_per_minute: int = 30):
        self._counts: dict = defaultdict(list)
        self._max = max_per_minute

    def check(self, key: str) -> bool:
        now = time.monotonic()
        calls = [t for t in self._counts[key] if now - t < 60]
        calls.append(now)
        self._counts[key] = calls
        return len(calls) <= self._max

# Module-level singleton
rate_limiter = RateLimiter(max_per_minute=30)
