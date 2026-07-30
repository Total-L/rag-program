"""Token-bucket rate limiter.

Common Q&A target: "implement rate limiting in pure Python".
"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    """Thread-safe token bucket."""

    def __init__(self, rate: float, capacity: float) -> None:
        """`rate` tokens/sec, max `capacity`."""
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be > 0")
        self._rate = float(rate)
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: float = 1.0) -> bool:
        """Try to take `n` tokens. Returns True if granted."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False
