from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def relative_time_label(value: datetime | None, now: datetime | None = None) -> str | None:
    if value is None:
        return None
    value = ensure_aware(value)
    now = ensure_aware(now) or utcnow()
    delta = now - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "agora"
    if seconds < 3600:
        minutes = max(seconds // 60, 1)
        return f"há {minutes}min"
    if seconds < 86400:
        hours = max(seconds // 3600, 1)
        return f"há {hours}h"
    days = max(seconds // 86400, 1)
    return f"há {days} dias"
