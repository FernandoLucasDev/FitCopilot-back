from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from http import HTTPStatus

from flask import current_app, request

from app.common.api import ApiError


_BUCKETS: dict[str, deque[datetime]] = defaultdict(deque)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def check_rate_limit(*, key: str, limit: int, window_seconds: int) -> None:
    if current_app.config.get("DISABLE_RATE_LIMITS"):
        return
    now = utcnow()
    cutoff = now - timedelta(seconds=window_seconds)
    bucket = _BUCKETS[key]
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        raise ApiError("Muitas tentativas. Aguarde um pouco e tente novamente.", HTTPStatus.TOO_MANY_REQUESTS)
    bucket.append(now)


def reset_rate_limits() -> None:
    _BUCKETS.clear()
