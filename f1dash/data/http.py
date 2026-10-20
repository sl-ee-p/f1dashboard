"""Shared HTTP client: disk cache, per-host throttling and retry on rate limits.

Every upstream (OpenF1, Jolpica, MultiViewer) is a free community API with rate
limits, so all traffic goes through one ``Fetcher``. Responses for finished
sessions never change, so callers can ask for them to be cached forever.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import requests
import requests_cache

log = logging.getLogger(__name__)

NEVER = requests_cache.NEVER_EXPIRE
SHORT = timedelta(minutes=5)
DAY = timedelta(hours=24)


class UpstreamError(RuntimeError):
    """An upstream API failed or is unreachable."""


class NotFound(UpstreamError):
    """The upstream API has no data for this request."""


class RateLimited(UpstreamError):
    """We are not sending requests to an upstream host right now.

    Either the request budget is spent, the host asked us to back off, or it
    just failed every retry and is cooling down.
    """


@dataclass
class HostBudget:
    """Token bucket: ``per_minute`` sustained, ``burst`` at once, ``spacing`` apart."""

    per_minute: float
    burst: int
    spacing: float = 0.0
    tokens: float = -1.0
    updated: float = 0.0
    last: float = 0.0
    blocked_until: float = 0.0
    blocked_reason: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def block(self, seconds: float, reason: str) -> None:
        """Refuse requests for ``seconds`` without touching the network."""
        self.blocked_until = time.monotonic() + seconds
        self.blocked_reason = reason

    def take(self, max_wait: float) -> None:
        with self.lock:
            now = time.monotonic()
            if self.tokens < 0:
                self.tokens, self.updated = float(self.burst), now
            if now < self.blocked_until:
                raise RateLimited(
                    f"{self.blocked_reason or 'backing off'}; "
                    f"next try in {self.blocked_until - now:.0f}s"
                )
            rate = self.per_minute / 60
            self.tokens = min(self.burst, self.tokens + (now - self.updated) * rate)
            self.updated = now
            wait = max(
                (1 - self.tokens) / rate if self.tokens < 1 else 0.0, self.last + self.spacing - now
            )
            if wait > max_wait:
                raise RateLimited(f"request budget exhausted, next slot in {wait:.0f}s")
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
                self.tokens = min(self.burst, self.tokens + wait * rate)
                self.updated = now
            self.tokens -= 1
            self.last = now


# OpenF1's free tier allows 3 requests/s and 30/min; Jolpica 4/s and 500/hour.
DEFAULT_BUDGETS = {
    "api.openf1.org": (30, 20, 0.4),
    "api.jolpi.ca": (8, 30, 0.3),
}


class Fetcher:
    def __init__(
        self,
        cache_dir: Path,
        min_interval: float = 0.5,
        retries: int = 3,
        timeout: float | tuple[float, float] = (5.0, 30.0),
        offline: bool = False,
        max_wait: float = 8.0,
        cooldown: float = 30.0,
    ) -> None:
        """``timeout`` is (connect, read) seconds per attempt.

        After a host fails every retry, calls to it fail fast (or serve stale
        data) for ``cooldown`` seconds instead of each one waiting through the
        timeouts and retries again.
        """
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests_cache.CachedSession(
            str(cache_dir / "http_cache"),
            backend="sqlite",
            expire_after=SHORT,
            # 404s are cached too: OpenF1 uses them for "no rows".
            allowable_codes=(200, 404),
            stale_if_error=True,
        )
        self.session.headers["User-Agent"] = "f1dash/0.2 (+homelab dashboard)"
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self.offline = offline
        self.max_wait = max_wait
        self.cooldown = cooldown
        self._budgets: dict[str, HostBudget] = {}
        self._guard = threading.Lock()

    def budget(self, host: str) -> HostBudget:
        with self._guard:
            if host not in self._budgets:
                per_min, burst, spacing = DEFAULT_BUDGETS.get(host, (120, 20, self.min_interval))
                self._budgets[host] = HostBudget(per_min, burst, max(spacing, self.min_interval))
            return self._budgets[host]

    def _cached(self, req, allow_expired: bool = False):
        key = self.session.cache.create_key(req)
        cached = self.session.cache.responses.get(key)
        if cached is None or (cached.is_expired and not allow_expired):
            return None
        return cached

    def get_json(self, url: str, params: dict | None = None, expire=SHORT):
        """GET ``url`` and return decoded JSON, served from cache when possible.

        Fresh cache hits never touch the network. When the upstream is rate
        limiting or down, an expired cached copy is used if there is one.
        """
        req = self.session.prepare_request(requests.Request("GET", url, params=params))
        hit = self._cached(req)
        if hit is not None:
            return self._decode(hit, url)
        if self.offline:
            raise UpstreamError(f"offline and not cached: {url}")

        host = urlsplit(url).netloc
        budget = self.budget(host)
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                budget.take(self.max_wait)
                resp = self.session.get(
                    url, params=params, expire_after=expire, timeout=self.timeout
                )
            except RateLimited as exc:
                return self._stale_or_raise(req, url, RateLimited(f"{exc} ({url} not cached)"))
            except requests.RequestException as exc:
                if attempt == self.retries:
                    budget.block(self.cooldown, f"{host} unreachable")
                    stale = self._cached(req, allow_expired=True)
                    if stale is not None:
                        return self._decode(stale, url)
                    raise UpstreamError(f"{host} unreachable: {exc}") from exc
                log.warning("request to %s failed (%s), retrying", host, exc)
                time.sleep(delay)
                delay *= 2
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "")
                pause = float(retry_after) if retry_after.isdigit() else 10.0
                budget.block(pause, f"{host} rate limited us")
                log.warning("%s rate limited us for %.0fs", host, pause)
                if pause > self.max_wait or attempt == self.retries:
                    error = RateLimited(f"rate limited and not cached: {url}")
                    return self._stale_or_raise(req, url, error)
                time.sleep(pause)
                budget.blocked_until = 0.0
                continue
            if resp.status_code >= 500:
                if attempt < self.retries:
                    log.warning("%s returned %s, retrying in %.1fs", host, resp.status_code, delay)
                    time.sleep(delay)
                    delay *= 2
                    continue
                budget.block(self.cooldown, f"{host} returned HTTP {resp.status_code}")
                error = UpstreamError(f"{url} returned HTTP {resp.status_code}")
                return self._stale_or_raise(req, url, error)
            return self._decode(resp, url)
        raise UpstreamError(f"{host} kept failing")  # pragma: no cover

    def _stale_or_raise(self, req, url: str, error: UpstreamError):
        stale = self._cached(req, allow_expired=True)
        if stale is not None:
            log.info("serving an expired cached copy of %s (%s)", url, error)
            return self._decode(stale, url)
        raise error

    @staticmethod
    def _decode(resp, url: str):
        if resp.status_code == 404:
            raise NotFound(url)
        if resp.status_code != 200:
            raise UpstreamError(f"{url} returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise UpstreamError(f"{url} returned invalid JSON") from exc
