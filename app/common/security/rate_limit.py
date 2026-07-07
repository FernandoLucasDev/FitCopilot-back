from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from http import HTTPStatus

from flask import current_app, request

from app.common.api import ApiError

logger = logging.getLogger(__name__)

# In-memory fallback (used only when Redis is unavailable)
_BUCKETS: dict[str, deque[datetime]] = defaultdict(deque)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def _get_redis():
    """Return a Redis client from the app config, or None."""
    try:
        import redis

        url = current_app.config.get("REDIS_URL")
        if not url:
            return None
        return redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    except Exception:
        return None


def _check_redis(r, *, key: str, limit: int, window_seconds: int) -> None:
    """Sliding-window rate limit backed by Redis sorted sets."""
    import time

    now = time.time()
    pipeline = r.pipeline()
    pipeline.zremrangebyscore(key, 0, now - window_seconds)
    pipeline.zcard(key)
    pipeline.zadd(key, {f"{now}": now})
    pipeline.expire(key, window_seconds + 10)
    results = pipeline.execute()
    count = results[1]
    if count >= limit:
        raise ApiError(
            "Muitas tentativas. Aguarde um pouco e tente novamente.",
            HTTPStatus.TOO_MANY_REQUESTS,
        )


def _check_memory(*, key: str, limit: int, window_seconds: int) -> None:
    """In-memory fallback (single-process only)."""
    now = utcnow()
    cutoff = now - timedelta(seconds=window_seconds)
    bucket = _BUCKETS[key]
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        raise ApiError(
            "Muitas tentativas. Aguarde um pouco e tente novamente.",
            HTTPStatus.TOO_MANY_REQUESTS,
        )
    bucket.append(now)


def check_rate_limit(*, key: str, limit: int, window_seconds: int) -> None:
    if current_app.config.get("DISABLE_RATE_LIMITS"):
        return
    prefixed_key = f"rl:{key}"
    r = _get_redis()
    if r:
        try:
            _check_redis(r, key=prefixed_key, limit=limit, window_seconds=window_seconds)
            return
        except ApiError:
            raise
        except Exception:
            logger.warning("Redis rate-limit failed, falling back to in-memory", exc_info=True)
    _check_memory(key=key, limit=limit, window_seconds=window_seconds)


def reset_rate_limits() -> None:
    _BUCKETS.clear()
    try:
        r = _get_redis()
        if not r:
            return
        for key in r.scan_iter("rl:*"):
            r.delete(key)
    except Exception:
        logger.warning("Redis rate-limit cleanup failed", exc_info=True)
