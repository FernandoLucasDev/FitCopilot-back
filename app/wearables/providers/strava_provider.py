from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from app.wearables.providers.base import WearableActivity, WearableProvider, WearableTokenResult
from app.wearables.providers.fake_provider import FakeWearableProvider

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
DEAUTHORIZE_URL = "https://www.strava.com/oauth/deauthorize"


class StravaWearableProvider(WearableProvider):
    source = "strava"

    def __init__(self, *, client_id: str | None, client_secret: str | None, redirect_uri: str | None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._fallback = FakeWearableProvider(callback_url=redirect_uri or "")
        self._configured = bool(client_id and client_secret and redirect_uri)

    def build_authorize_url(self, *, state: str) -> str:
        if not self._configured:
            return self._fallback.build_authorize_url(state=state)
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": "read,activity:read",
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, *, code: str) -> WearableTokenResult:
        if not self._configured:
            return self._fallback.exchange_code(code=code)
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            return WearableTokenResult(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                expires_at=datetime.fromtimestamp(data["expires_at"], tz=timezone.utc),
                external_athlete_id=str((data.get("athlete") or {}).get("id") or ""),
                scope="read,activity:read",
            )
        except Exception:
            return self._fallback.exchange_code(code=code)

    def refresh_token(self, *, refresh_token: str) -> WearableTokenResult:
        if not self._configured:
            return self._fallback.refresh_token(refresh_token=refresh_token)
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            return WearableTokenResult(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", refresh_token),
                expires_at=datetime.fromtimestamp(data["expires_at"], tz=timezone.utc),
                external_athlete_id="",
            )
        except Exception:
            return self._fallback.refresh_token(refresh_token=refresh_token)

    def fetch_recent_activities(self, *, access_token: str, since: datetime) -> list[WearableActivity]:
        if not self._configured:
            return self._fallback.fetch_recent_activities(access_token=access_token, since=since)
        try:
            response = requests.get(
                ACTIVITIES_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"after": int(since.timestamp()), "per_page": 50},
                timeout=10,
            )
            response.raise_for_status()
            items = response.json()
            activities: list[WearableActivity] = []
            for item in items:
                moving_time_seconds = item.get("moving_time") or 0
                if not moving_time_seconds:
                    continue
                activities.append(
                    WearableActivity(
                        external_id=str(item["id"]),
                        metric_type="active_minutes",
                        value=round(moving_time_seconds / 60, 1),
                        unit="minutes",
                        recorded_at=datetime.fromisoformat(item["start_date"].replace("Z", "+00:00")),
                        payload={
                            "activity_type": item.get("type"),
                            "distance_meters": item.get("distance"),
                            "name": item.get("name"),
                        },
                    )
                )
            return activities
        except Exception:
            return self._fallback.fetch_recent_activities(access_token=access_token, since=since)

    def revoke(self, *, access_token: str) -> None:
        if not self._configured:
            return self._fallback.revoke(access_token=access_token)
        try:
            requests.post(DEAUTHORIZE_URL, params={"access_token": access_token}, timeout=10)
        except Exception:
            pass
