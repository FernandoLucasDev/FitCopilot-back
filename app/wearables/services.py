from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from http import HTTPStatus

from flask import current_app

from app.common.api import ApiError
from app.common.security.crypto import decrypt_secret, encrypt_secret
from app.events.models import EventSource, EventType
from app.extensions import db
from app.operations.services import emit_event
from app.students.models import StudentProfile
from app.wearables.models import WearableConnectChallenge, WearableConnection, WearableDataPoint

CHALLENGE_TTL_MINUTES = 10
DEFAULT_SYNC_LOOKBACK_DAYS = 30


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _provider():
    return current_app.extensions["wearable_provider"]


def start_wearable_connect(*, student: StudentProfile, provider: str = "strava") -> dict:
    state_token = secrets.token_urlsafe(32)
    challenge = WearableConnectChallenge(
        student_id=student.id,
        provider=provider,
        state_token=state_token,
        expires_at=utcnow() + timedelta(minutes=CHALLENGE_TTL_MINUTES),
    )
    db.session.add(challenge)
    db.session.commit()
    authorize_url = _provider().build_authorize_url(state=state_token)
    return {"authorizeUrl": authorize_url, "provider": provider}


def complete_wearable_connect(*, code: str, state: str) -> dict:
    challenge = WearableConnectChallenge.query.filter_by(state_token=state, consumed_at=None).first()
    if challenge is None or _ensure_aware(challenge.expires_at) < utcnow():
        raise ApiError("Link de conexão inválido ou expirado", HTTPStatus.BAD_REQUEST)

    student = StudentProfile.query.filter_by(id=challenge.student_id).first()
    if student is None:
        raise ApiError("Aluno não encontrado", HTTPStatus.NOT_FOUND)

    provider = _provider()
    token = provider.exchange_code(code=code)
    challenge.consumed_at = utcnow()

    connection = WearableConnection.query.filter_by(student_id=student.id, source=provider.source).first()
    if connection is None:
        connection = WearableConnection(
            student_id=student.id,
            account_id=student.account_id,
            source=provider.source,
            connected_at=utcnow(),
        )
        db.session.add(connection)

    connection.external_athlete_id = token.external_athlete_id
    connection.access_token_encrypted = encrypt_secret(token.access_token)
    connection.refresh_token_encrypted = encrypt_secret(token.refresh_token) if token.refresh_token else None
    connection.token_expires_at = token.expires_at
    connection.scope = token.scope
    connection.revoked_at = None
    connection.connected_at = utcnow()

    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type=EventType.WEARABLE_CONNECTED,
        source=EventSource.PORTAL,
        title=f"Wearable conectado ({provider.source})",
        payload={"provider": provider.source},
    )
    db.session.commit()

    sync_result = sync_student_wearable_data(connection)
    return {"connected": True, "provider": provider.source, "studentId": str(student.id), "sync": sync_result}


def disconnect_wearable(*, student: StudentProfile, source: str = "strava") -> dict:
    connection = WearableConnection.query.filter_by(student_id=student.id, source=source, revoked_at=None).first()
    if connection is None:
        raise ApiError("Nenhuma conexão ativa encontrada", HTTPStatus.NOT_FOUND)

    provider = _provider()
    try:
        provider.revoke(access_token=decrypt_secret(connection.access_token_encrypted))
    except Exception:
        pass
    connection.revoked_at = utcnow()

    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type=EventType.WEARABLE_DISCONNECTED,
        source=EventSource.PORTAL,
        title=f"Wearable desconectado ({source})",
        payload={"provider": source},
    )
    db.session.commit()
    return {"connected": False, "provider": source}


def get_active_connection(student: StudentProfile, *, source: str = "strava") -> WearableConnection | None:
    return WearableConnection.query.filter_by(student_id=student.id, source=source, revoked_at=None).first()


def sync_student_wearable_data(connection: WearableConnection) -> dict:
    provider = _provider()
    since = _ensure_aware(connection.last_synced_at) if connection.last_synced_at else utcnow() - timedelta(days=DEFAULT_SYNC_LOOKBACK_DAYS)

    try:
        access_token = decrypt_secret(connection.access_token_encrypted)
        activities = provider.fetch_recent_activities(access_token=access_token, since=since)
    except Exception as exc:
        connection.last_sync_status = "error"
        connection.last_synced_at = utcnow()
        db.session.commit()
        return {"status": "error", "created": 0, "error": str(exc)}

    created = 0
    for activity in activities:
        exists = WearableDataPoint.query.filter_by(
            student_id=connection.student_id, source=provider.source, external_id=activity.external_id
        ).first()
        if exists is not None:
            continue
        db.session.add(
            WearableDataPoint(
                student_id=connection.student_id,
                account_id=connection.account_id,
                source=provider.source,
                metric_type=activity.metric_type,
                value=activity.value,
                unit=activity.unit,
                recorded_at=_ensure_aware(activity.recorded_at),
                synced_at=utcnow(),
                external_id=activity.external_id,
                payload_json=activity.payload,
            )
        )
        created += 1

    connection.last_synced_at = utcnow()
    connection.last_sync_status = "ok"

    if created:
        emit_event(
            account_id=connection.account_id,
            student_id=connection.student_id,
            event_type=EventType.WEARABLE_SYNC_COMPLETED,
            source=EventSource.CELERY,
            title=f"Sincronização de wearable: {created} novo(s) registro(s)",
            payload={"provider": provider.source, "created": created},
        )
    db.session.commit()
    return {"status": "ok", "created": created}


def get_recent_wearable_metrics(student: StudentProfile, *, days: int = 14) -> dict | None:
    """Leitura pura (sem side effects) usada pelo motor de score. Retorna None sem dado algum."""
    since = utcnow() - timedelta(days=days)
    points = (
        WearableDataPoint.query.filter(
            WearableDataPoint.student_id == student.id,
            WearableDataPoint.metric_type == "active_minutes",
            WearableDataPoint.recorded_at >= since,
        )
        .order_by(WearableDataPoint.recorded_at.asc())
        .all()
    )
    if not points:
        return None

    daily_totals: dict[str, float] = {}
    for point in points:
        day_key = point.recorded_at.date().isoformat()
        daily_totals[day_key] = daily_totals.get(day_key, 0.0) + point.value

    recent_days = sorted(daily_totals.keys())[-3:]
    baseline_days = sorted(daily_totals.keys())
    recent_avg = sum(daily_totals[day] for day in recent_days) / len(recent_days) if recent_days else 0.0
    baseline_avg = sum(daily_totals[day] for day in baseline_days) / len(baseline_days) if baseline_days else 0.0
    days_with_activity = len(daily_totals)

    return {
        "dailyTotals": daily_totals,
        "recentAvgActiveMinutes": round(recent_avg, 1),
        "baselineAvgActiveMinutes": round(baseline_avg, 1),
        "daysWithActivity": days_with_activity,
        "windowDays": days,
    }


def wearable_time_series(student: StudentProfile, *, days: int = 30) -> list[dict]:
    since = utcnow() - timedelta(days=days)
    points = (
        WearableDataPoint.query.filter(WearableDataPoint.student_id == student.id, WearableDataPoint.recorded_at >= since)
        .order_by(WearableDataPoint.recorded_at.asc())
        .all()
    )
    return [
        {
            "date": point.recorded_at.date().isoformat(),
            "metricType": point.metric_type,
            "value": point.value,
            "unit": point.unit,
            "source": point.source,
        }
        for point in points
    ]


def serialize_wearable_summary(student: StudentProfile) -> dict:
    from app.wearables.alerts import list_recent_wearable_alerts

    connection = get_active_connection(student)
    return {
        "connected": connection is not None,
        "source": connection.source if connection else None,
        "connectedAt": connection.connected_at.isoformat() if connection else None,
        "lastSyncedAt": connection.last_synced_at.isoformat() if connection and connection.last_synced_at else None,
        "lastSyncStatus": connection.last_sync_status if connection else None,
        "series": wearable_time_series(student) if connection else [],
        "alerts": list_recent_wearable_alerts(student) if connection else [],
    }
