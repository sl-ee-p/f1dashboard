"""A tiny in-memory rate limiter for endpoints that hit upstream APIs hard."""

import threading
import time
from functools import wraps

from flask import abort, request


class TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self.rate, self.burst = rate, burst
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1, now)
            if len(self._buckets) > 10_000:  # forget idle clients
                self._buckets.clear()
            return True


def limited(rate: float, burst: int):
    bucket = TokenBucket(rate, burst)

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not bucket.allow(request.remote_addr or "?"):
                abort(429)
            return fn(*args, **kwargs)

        return wrapper

    return deco
