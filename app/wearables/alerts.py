from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.events.models import EventSource, EventType
from app.extensions import db
from app.operations.models import AutomationDecision
from app.operations.services import emit_event
from app.students.models import StudentProfile
from app.wearables.models import WearableConnection, WearableDataPoint
from app.wearables.services import get_active_connection, get_recent_wearable_metrics

WEARABLE_RULE_TYPES = ("wearable_activity_drop", "wearable_inactivity", "wearable_low_sleep")
COOLDOWN_HOURS = 72


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _recent_decision(student: StudentProfile, rule_type: str) -> AutomationDecision | None:
    return AutomationDecision.query.filter(
        AutomationDecision.student_id == student.id,
        AutomationDecision.rule_type == rule_type,
        AutomationDecision.created_at >= utcnow() - timedelta(hours=COOLDOWN_HOURS),
    ).first()


def _create_alert(*, student: StudentProfile, rule_type: str, priority: str, reason: str, action: str, payload: dict) -> AutomationDecision:
    decision = AutomationDecision(
        account_id=student.account_id,
        student_id=student.id,
        rule_type=rule_type,
        status="suggested",
        priority=priority,
        reason=reason,
        suggested_action=action,
        suppressed_until=utcnow() + timedelta(hours=COOLDOWN_HOURS),
        payload_json=payload,
    )
    db.session.add(decision)
    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type=EventType.WEARABLE_ALERT_TRIGGERED,
        source=EventSource.WEARABLE,
        title=action,
        body=reason,
        severity="warning" if priority == "medium" else "critical",
        payload={"rule_type": rule_type, **payload},
    )
    db.session.flush()
    return decision


def _evaluate_activity_drop(student: StudentProfile, connection: WearableConnection) -> AutomationDecision | None:
    metrics = get_recent_wearable_metrics(student, days=17)
    if metrics is None or metrics["daysWithActivity"] < 4 or metrics["baselineAvgActiveMinutes"] <= 0:
        return None
    drop_ratio = 1 - (metrics["recentAvgActiveMinutes"] / metrics["baselineAvgActiveMinutes"])
    if drop_ratio < 0.4:
        return None
    if _recent_decision(student, "wearable_activity_drop") is not None:
        return None
    return _create_alert(
        student=student,
        rule_type="wearable_activity_drop",
        priority="medium",
        reason=f"Queda de {round(drop_ratio * 100)}% na atividade recente (wearable) comparado à média do aluno.",
        action="Confirmar com o aluno se está tudo bem — possível lesão, fadiga ou desmotivação.",
        payload={"dropRatio": round(drop_ratio, 2), "recentAvg": metrics["recentAvgActiveMinutes"], "baselineAvg": metrics["baselineAvgActiveMinutes"]},
    )


def _evaluate_inactivity(student: StudentProfile, connection: WearableConnection) -> AutomationDecision | None:
    connected_days_ago = (utcnow() - _ensure_aware(connection.connected_at)).days
    if connected_days_ago < 5:
        return None
    since = utcnow() - timedelta(days=5)
    recent_point = WearableDataPoint.query.filter(
        WearableDataPoint.student_id == student.id,
        WearableDataPoint.metric_type == "active_minutes",
        WearableDataPoint.recorded_at >= since,
    ).first()
    if recent_point is not None:
        return None
    if _recent_decision(student, "wearable_inactivity") is not None:
        return None
    return _create_alert(
        student=student,
        rule_type="wearable_inactivity",
        priority="medium",
        reason="Nenhuma atividade registrada no wearable nos últimos 5 dias.",
        action="Fazer um check-in leve — pode ser pausa merecida, lesão ou queda de motivação.",
        payload={"windowDays": 5},
    )


def _evaluate_low_sleep(student: StudentProfile, connection: WearableConnection) -> AutomationDecision | None:
    since = utcnow() - timedelta(days=3)
    points = WearableDataPoint.query.filter(
        WearableDataPoint.student_id == student.id,
        WearableDataPoint.metric_type == "sleep_hours",
        WearableDataPoint.recorded_at >= since,
    ).all()
    if len(points) < 3:
        return None
    avg_sleep = sum(point.value for point in points) / len(points)
    if avg_sleep >= 5.5:
        return None
    if _recent_decision(student, "wearable_low_sleep") is not None:
        return None
    return _create_alert(
        student=student,
        rule_type="wearable_low_sleep",
        priority="high",
        reason=f"Média de {round(avg_sleep, 1)}h de sono nas últimas 3 noites, abaixo do recomendado.",
        action="Sugerir ajuste de volume/intensidade do treino até o sono normalizar.",
        payload={"avgSleepHours": round(avg_sleep, 1)},
    )


def evaluate_wearable_alerts(student: StudentProfile) -> AutomationDecision | None:
    connection = get_active_connection(student)
    if connection is None:
        return None
    for evaluator in (_evaluate_activity_drop, _evaluate_inactivity, _evaluate_low_sleep):
        decision = evaluator(student, connection)
        if decision is not None:
            db.session.commit()
            return decision
    return None


def list_recent_wearable_alerts(student: StudentProfile, *, limit: int = 5) -> list[dict]:
    decisions = (
        AutomationDecision.query.filter(
            AutomationDecision.student_id == student.id,
            AutomationDecision.rule_type.in_(WEARABLE_RULE_TYPES),
        )
        .order_by(AutomationDecision.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(item.id),
            "ruleType": item.rule_type,
            "priority": item.priority,
            "reason": item.reason,
            "suggestedAction": item.suggested_action,
            "createdAt": item.created_at.isoformat(),
        }
        for item in decisions
    ]
