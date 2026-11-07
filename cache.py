"""
Redis-backed caching and rate limiting, with an in-process fallback.

    REDIS_URL=redis://redis:6379/0     -> shared across processes/pods
    REDIS_URL unset                    -> per-process memory (local dev, tests)

Used for:
- supplier search results (SEARCH_CACHE_TTL seconds, default 300; 0 disables). Supplier
  APIs are slow and rate-limited; identical searches within a few minutes reuse results.
- geocoding look-ups (long TTL).
- the login rate limiter, so the limit holds across all API replicas.

Redis is an optimisation, never a dependency for correctness: if it is down, cache reads
miss, writes are skipped, and the rate limiter falls back to per-process counting.
Sessions are deliberately NOT cached, so logout/revocation takes effect immediately.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger("cache")

_client = None
_client_url = None
_lock = threading.Lock()
_last_error_log = 0.0


def _prefix() -> str:
    return os.environ.get("CACHE_PREFIX", "booking:")


def redis_url() -> str:
    return os.environ.get("REDIS_URL", "").strip()


class _Memory:
    """Tiny TTL store with the same semantics, for when Redis isn't configured or is down."""

    def __init__(self):
        self.data: dict[str, tuple[float, Any]] = {}
        self.lock = threading.Lock()

    def get(self, key: str):
        with self.lock:
            item = self.data.get(key)
            if not item:
                return None
            if item[0] < time.monotonic():
                del self.data[key]
                return None
            return item[1]

    def set(self, key: str, value, ttl: float) -> None:
        with self.lock:
            if len(self.data) > 10_000:  # crude bound: drop expired entries
                now = time.monotonic()
                self.data = {k: v for k, v in self.data.items() if v[0] > now}
            self.data[key] = (time.monotonic() + ttl, value)

    def incr(self, key: str, window: float) -> int:
        with self.lock:
            expires, count = self.data.get(key, (0.0, 0))
            if expires < time.monotonic():
                expires, count = time.monotonic() + window, 0
            self.data[key] = (expires, count + 1)
            return count + 1


_memory = _Memory()


def _redis():
    """The Redis client, or None when not configured."""
    global _client, _client_url
    url = redis_url()
    if not url:
        return None
    with _lock:
        if _client is None or _client_url != url:
            import redis

            _client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=0.5,
                                           socket_connect_timeout=0.5, health_check_interval=30)
            _client_url = url
        return _client


def _warn(e: Exception) -> None:
    global _last_error_log
    if time.monotonic() - _last_error_log > 60:  # don't flood logs while Redis is down
        _last_error_log = time.monotonic()
        log.warning("Redis unavailable, using fallback: %s", e)


def get_json(key: str) -> Any | None:
    key = _prefix() + key
    r = _redis()
    if r is None:
        return _memory.get(key)
    try:
        raw = r.get(key)
        return json.loads(raw) if raw is not None else None
    except Exception as e:  # noqa: BLE001
        _warn(e)
        return None


def set_json(key: str, value: Any, ttl: float) -> None:
    if ttl <= 0:
        return
    key = _prefix() + key
    r = _redis()
    if r is None:
        _memory.set(key, value, ttl)
        return
    try:
        r.set(key, json.dumps(value, separators=(",", ":")), ex=max(1, int(ttl)))
    except Exception as e:  # noqa: BLE001
        _warn(e)


def key_for(namespace: str, *parts: Any) -> str:
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]
    return f"{namespace}:{digest}"


def cached(namespace: str, ttl: float, parts: tuple, compute: Callable[[], Any],
           should_cache: Callable[[Any], bool] = bool) -> Any:
    """Return the cached value for (namespace, parts), computing and storing it on a miss."""
    if ttl <= 0:
        return compute()
    key = key_for(namespace, *parts)
    hit = get_json(key)
    if hit is not None:
        return hit
    value = compute()
    if should_cache(value):
        set_json(key, value, ttl)
    return value


def hit_rate_limit(bucket: str, limit: int, window_seconds: int) -> bool:
    """Count one hit in a fixed window; True when the limit is exceeded."""
    key = f"{_prefix()}rl:{bucket}:{int(time.time() // window_seconds)}"
    r = _redis()
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds + 5)
            count = pipe.execute()[0]
            return count > limit
        except Exception as e:  # noqa: BLE001
            _warn(e)
    return _memory.incr(key, window_seconds) > limit


def ping() -> bool | None:
    """True/False for Redis health, None when Redis isn't configured."""
    r = _redis()
    if r is None:
        return None
    try:
        return bool(r.ping())
    except Exception as e:  # noqa: BLE001
        _warn(e)
        return False


def search_ttl() -> int:
    return int(os.environ.get("SEARCH_CACHE_TTL", "300"))
