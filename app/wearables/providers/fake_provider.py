from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from app.wearables.providers.base import WearableActivity, WearableProvider, WearableTokenResult


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FakeWearableProvider(WearableProvider):
    """Provider determinístico para desenvolvimento local, sem chamar nenhuma API externa.

    `build_authorize_url` aponta de volta para o próprio callback (com um código falso já
    embutido), então o fluxo completo (conectar → callback → sincronizar) roda ponta a ponta
    localmente, com o mesmo código de rota usado em produção — só o provider muda.
    """

    source = "strava"

    def __init__(self, *, callback_url: str):
        self.callback_url = callback_url

    def build_authorize_url(self, *, state: str) -> str:
        return f"{self.callback_url}?code=fake-{state}&state={state}"

    def exchange_code(self, *, code: str) -> WearableTokenResult:
        seed = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
        return WearableTokenResult(
            access_token=f"fake-access-{seed}",
            refresh_token=f"fake-refresh-{seed}",
            expires_at=_utcnow() + timedelta(hours=6),
            external_athlete_id=f"fake-athlete-{seed}",
            scope="read,activity:read",
        )

    def refresh_token(self, *, refresh_token: str) -> WearableTokenResult:
        seed = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:12]
        return WearableTokenResult(
            access_token=f"fake-access-{uuid.uuid4().hex[:12]}",
            refresh_token=refresh_token,
            expires_at=_utcnow() + timedelta(hours=6),
            external_athlete_id=f"fake-athlete-{seed}",
        )

    def fetch_recent_activities(self, *, access_token: str, since: datetime) -> list[WearableActivity]:
        seed = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
        now = _utcnow()
        days = max(0, (now.date() - since.date()).days)
        activities: list[WearableActivity] = []
        for offset in range(min(days, 7)):
            day = now - timedelta(days=offset)
            day_seed = int(seed[offset % len(seed)], 16)
            if day_seed % 6 == 0:
                continue  # dia sem atividade registrada, como no mundo real
            active_minutes = 15 + (day_seed % 5) * 10
            activities.append(
                WearableActivity(
                    external_id=f"fake-active-{seed[:8]}-{day.date().isoformat()}",
                    metric_type="active_minutes",
                    value=float(active_minutes),
                    unit="minutes",
                    recorded_at=day,
                    payload={"source": "fake", "activity_type": "Run" if day_seed % 2 == 0 else "Ride"},
                )
            )
        return activities

    def revoke(self, *, access_token: str) -> None:
        return None
