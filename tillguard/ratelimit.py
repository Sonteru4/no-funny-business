"""Simple in-memory rate limiter for desk endpoints."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, bucket: str, limit: int, window_seconds: float = 60.0) -> dict:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            q = self._hits[bucket]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                retry_after = max(1, int(window_seconds - (now - q[0]) + 0.99))
                return {"ok": False, "retry_after": retry_after, "limit": limit}
            q.append(now)
            return {"ok": True, "remaining": limit - len(q), "limit": limit}


LIMITER = RateLimiter()
