from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class WearableTokenResult:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    external_athlete_id: str
    scope: str | None = None


@dataclass
class WearableActivity:
    external_id: str
    metric_type: str  # "active_minutes" | "steps" | "sleep_hours" | "resting_hr"
    value: float
    unit: str
    recorded_at: datetime
    payload: dict


class WearableProvider:
    source = "generic"

    def build_authorize_url(self, *, state: str) -> str:
        raise NotImplementedError

    def exchange_code(self, *, code: str) -> WearableTokenResult:
        raise NotImplementedError

    def refresh_token(self, *, refresh_token: str) -> WearableTokenResult:
        raise NotImplementedError

    def fetch_recent_activities(self, *, access_token: str, since: datetime) -> list[WearableActivity]:
        raise NotImplementedError

    def revoke(self, *, access_token: str) -> None:
        raise NotImplementedError
