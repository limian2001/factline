"""HTTP plumbing shared by every data source: rate limiting, retry, disk cache.

This module exists because all three concerns are easy to get wrong once and then
wrong everywhere. The SEC blocks an IP for roughly ten minutes when you exceed
10 req/s, so the limiter is not advisory -- it is the difference between a pipeline
that finishes and one that dies halfway with a partial dataset.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)


class RateLimitedError(RuntimeError):
    """Server told us to slow down (429) or blocked us (403 after throttling)."""


class TransientHTTPError(RuntimeError):
    """5xx or network-level failure worth retrying."""


@dataclass
class TokenBucket:
    """Classic token bucket, thread-safe, with an injectable clock for testing.

    A bucket rather than a fixed sleep because real workloads are bursty: we want
    to spend accumulated headroom immediately and only block once it is gone.
    """

    rate: float
    capacity: float | None = None
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    _tokens: float = field(init=False)
    _last: float = field(init=False)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError("rate must be positive")
        if self.capacity is None:
            self.capacity = self.rate
        self._tokens = float(self.capacity)
        self._last = self.monotonic()

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until `tokens` are available. Returns seconds actually waited."""
        assert self.capacity is not None
        if tokens > self.capacity:
            raise ValueError(f"cannot acquire {tokens} from a bucket of {self.capacity}")

        with self._lock:
            now = self.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0

            deficit = tokens - self._tokens
            wait = deficit / self.rate
            self._tokens = 0.0
            self._last = now + wait

        self.sleep(wait)
        return wait


class DiskCache:
    """Content cache keyed by URL. TTL-based, JSON payloads only.

    EDGAR data changes at most once a day; re-fetching it during development is
    both slow and rude. The cache makes a full rebuild of the universe nearly free
    after the first run.
    """

    def __init__(self, root: Path, ttl_hours: int = 24) -> None:
        self.root = root
        self.ttl_seconds = ttl_hours * 3600
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:32]
        return self.root / f"{digest}.json"

    def get(self, url: str) -> Any | None:
        path = self._path(url)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.ttl_seconds:
            log.debug("cache.expired", url=url, age_hours=round(age / 3600, 1))
            return None
        try:
            with path.open() as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            log.warning("cache.unreadable", path=str(path))
            return None

    def put(self, url: str, payload: Any) -> None:
        path = self._path(url)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as fh:
            json.dump(payload, fh)
        tmp.replace(path)  # atomic; a crash mid-write never leaves a corrupt entry


class JSONAPIClient:
    """A JSON HTTP client that is polite by construction.

    Every subclass gets rate limiting, retry with backoff, and caching without
    having to remember to add them.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        rate_limit_rps: float = 8.0,
        cache: DiskCache | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bucket = TokenBucket(rate=rate_limit_rps)
        self.cache = cache
        self._client = client or httpx.Client(
            headers=headers or {},
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JSONAPIClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, path: str, *, use_cache: bool = True) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"

        if use_cache and self.cache is not None:
            hit = self.cache.get(url)
            if hit is not None:
                log.debug("http.cache_hit", url=url)
                return hit

        payload = self._fetch_with_retry(url)

        if use_cache and self.cache is not None:
            self.cache.put(url, payload)
        return payload

    @retry(
        retry=retry_if_exception_type((TransientHTTPError, RateLimitedError)),
        wait=wait_exponential(multiplier=2, min=2, max=120),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _fetch_with_retry(self, url: str) -> Any:
        waited = self.bucket.acquire()
        if waited:
            log.debug("http.throttled", url=url, waited_s=round(waited, 3))

        try:
            response = self._client.get(url)
        except httpx.RequestError as exc:
            raise TransientHTTPError(f"network error for {url}: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitedError(f"429 from {url}")
        if response.status_code == 403:
            # For the SEC this almost always means a missing/bad User-Agent, or a
            # throttle block. Say so, because the raw 403 is famously unhelpful.
            raise RateLimitedError(
                f"403 from {url}. Check SEC_USER_AGENT is set to 'Name email@example.com', "
                "or you may be throttle-blocked (~10 min)."
            )
        if response.status_code >= 500:
            raise TransientHTTPError(f"{response.status_code} from {url}")

        response.raise_for_status()
        return response.json()
