import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Token-bucket rate limiter keyed by client identifier (e.g. IP address)."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max = max_requests
        self._rate = max_requests / window_seconds   # tokens / second
        self._buckets: dict[str, _Bucket] = defaultdict(
            lambda: _Bucket(tokens=float(self._max))
        )
        self._lock = asyncio.Lock()

    async def is_allowed(self, client_id: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets[client_id]
            elapsed = now - bucket.last_refill
            bucket.tokens = min(self._max, bucket.tokens + elapsed * self._rate)
            bucket.last_refill = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False
