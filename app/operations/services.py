from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import desc

from app.events.models import StudentEvent, StudentHealthScore
from app.extensions import db
from app.insights.models import AIInsight
from app.messaging.models import SuggestedMessage
from app.operations.models import AutomationDecision
from app.students.models import StudentDailySignal, StudentInteraction, StudentProfile
from app.workouts.models import WorkoutSession


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def emit_event(
    *,
    account_id,
    student_id=None,
    event_type: str,
    source: str,
    title: str,
    body: str | None = None,
    occurred_at: datetime | None = None,
    event_key: str | None = None,
    severity: str = "info",
    payload: dict | None = None,
) -> StudentEvent:
    payload_json = payload or {}
    if event_key:
        payload_json = {**payload_json, "event_key": event_key}
    event = StudentEvent(
        account_id=account_id,
        student_id=student_id,
        event_type=event_type,
        source=source,
        title=title,
        body=body,
        occurred_at=occurred_at or utcnow(),
        created_at=utcnow(),
        payload_json={**payload_json, "severity": severity},
    )
    db.session.add(event)
    db.session.flush()
    return event


@dataclass
class OperationalScore:
    score: int
    status: str
    trend: str
    risk_level: str
    reason: str
    factors: dict


def calculate_student_health_score(student: StudentProfile) -> OperationalScore:
    now = utcnow()
    since = now - timedelta(days=7)
    score = 72
    factors: dict[str, int | str | None] = {}
    reasons: list[str] = []

    last_activity = ensure_aware(student.last_activity_at) if student.last_activity_at else None
    inactivity_days = (now - last_activity).days if last_activity else 999
    factors["inactivity_days"] = inactivity_days if inactivity_days != 999 else None
    if inactivity_days >= 5:
        score -= 28
        reasons.append("sem atividade ha 5+ dias")
    elif inactivity_days >= 3:
        score -= 18
        reasons.append("sumiu por 3+ dias")
    elif inactivity_days >= 2:
        score -= 10
        reasons.append("atividade caiu nos ultimos dias")

    last_contact = ensure_aware(student.last_contact_at) if student.last_contact_at else None
    no_response_days = (now - last_contact).days if last_contact else 999
    factors["no_response_days"] = no_response_days if no_response_days != 999 else None
    if no_response_days >= 4:
        score -= 18
        reasons.append("sem resposta recente")
    elif no_response_days >= 2:
        score -= 8
        reasons.append("resposta esfriando")

    signals = StudentDailySignal.query.filter(
        StudentDailySignal.student_id == student.id,
        StudentDailySignal.created_at >= since,
    ).all()
    workouts = [item for item in signals if item.signal_type == "workout"]
    meals = [item for item in signals if item.signal_type == "meal"]
    absences = [item for item in signals if item.signal_type == "absence"]
    negative_notes = [
        item
        for item in signals
        if item.signal_type == "manual_note" and any(word in (item.title or "").lower() for word in ["ignor", "nao", "falt", "pesado"])
    ]
    sessions = WorkoutSession.query.filter(
        WorkoutSession.student_id == student.id,
        WorkoutSession.session_date >= date.today() - timedelta(days=7),
    ).all()
    skipped_sessions = [item for item in sessions if item.status == "skipped"]
    completed_sessions = [item for item in sessions if item.status == "completed"]

    factors.update(
        {
            "workouts_7d": len(workouts) + len(completed_sessions),
            "meals_7d": len(meals),
            "absences_7d": len(absences),
            "skipped_workouts_7d": len(skipped_sessions),
        }
    )
    score += min((len(workouts) + len(completed_sessions)) * 4, 12)
    score += min(len(meals) * 2, 8)
    score -= min((len(absences) + len(skipped_sessions)) * 8, 24)
    score -= min(len(negative_notes) * 5, 15)

    if len(skipped_sessions) >= 2:
        reasons.append("treinos ignorados se acumulando")
    if len(meals) == 0 and len(workouts) + len(completed_sessions) == 0 and inactivity_days >= 2:
        reasons.append("sem sinais de treino ou refeicao")
    if len(meals) >= 2 or len(workouts) + len(completed_sessions) >= 2:
        reasons.append("ha sinais positivos recentes")

    # Dimensao de wearable (atividade passiva) — so contribui quando ha dado conectado,
    # o score continua valido e sem penalidade para quem nao conectou nenhum wearable.
    from app.wearables.services import get_recent_wearable_metrics

    wearable_metrics = get_recent_wearable_metrics(student, days=7)
    factors["wearable_active_minutes_avg"] = wearable_metrics["recentAvgActiveMinutes"] if wearable_metrics else None
    if wearable_metrics is not None:
        recent_avg = wearable_metrics["recentAvgActiveMinutes"]
        if recent_avg >= 30:
            score += 12
            reasons.append("boa atividade fisica registrada no wearable")
        elif recent_avg >= 15:
            score += 6
            reasons.append("atividade fisica moderada no wearable")
        elif recent_avg > 0:
            reasons.append("atividade fisica baixa no wearable")
        else:
            score -= 6
            reasons.append("sem atividade fisica registrada no wearable nos ultimos dias")

    score = max(0, min(100, score))
    previous = (
        StudentHealthScore.query.filter_by(student_id=student.id, score_type="operational")
        .order_by(StudentHealthScore.computed_at.desc())
        .first()
    )
    if previous and score < previous.raw_score - 5:
        trend = "down"
    elif previous and score > previous.raw_score + 5:
        trend = "up"
    else:
        trend = student.adherence_trend or "stable"

    if score < 40:
        status = "risk"
        risk_level = "critical"
    elif score < 58:
        status = "cooling"
        risk_level = "high"
    elif score < 72:
        status = "attention"
        risk_level = "medium"
    else:
        status = "ok"
        risk_level = "ok"

    if not reasons:
        reasons.append("rotina recente sem alerta importante")
    return OperationalScore(score=score, status=status, trend=trend, risk_level=risk_level, reason="; ".join(reasons), factors=factors)


def recompute_and_persist_score(student: StudentProfile, *, emit: bool = True) -> StudentHealthScore:
    result = calculate_student_health_score(student)
    previous = (
        StudentHealthScore.query.filter_by(student_id=student.id, score_type="operational")
        .order_by(StudentHealthScore.computed_at.desc())
        .first()
    )
    previous_score = previous.raw_score if previous else None
    previous_level = previous.level if previous else None
    snapshot = StudentHealthScore.query.filter_by(
        student_id=student.id, score_date=date.today(), score_type="operational"
    ).first()
    if snapshot is None:
        snapshot = StudentHealthScore(
            account_id=student.account_id,
            student_id=student.id,
            score_date=date.today(),
            score_type="operational",
            raw_score=result.score,
            level=result.status,
            trend=result.trend,
            components_json={"reason": result.reason, "factors": result.factors, "risk_level": result.risk_level},
            computed_at=utcnow(),
        )
        db.session.add(snapshot)
    else:
        snapshot.raw_score = result.score
        snapshot.level = result.status
        snapshot.trend = result.trend
        snapshot.components_json = {"reason": result.reason, "factors": result.factors, "risk_level": result.risk_level}
        snapshot.computed_at = utcnow()
    student.adherence_score = result.score
    student.adherence_trend = result.trend
    if student.status != "archived":
        student.status = {"ok": "active", "attention": "attention", "cooling": "no_signal", "risk": "no_signal"}[result.status]
    student.last_signal_summary = result.reason

    if emit and (previous_score is None or previous_level != result.status or abs(previous_score - result.score) >= 8):
        emit_event(
            account_id=student.account_id,
            student_id=student.id,
            event_type="score_changed",
            source="score_engine",
            title=f"Score operacional: {result.score} ({result.status})",
            body=result.reason,
            severity="warning" if result.status in {"attention", "cooling"} else "critical" if result.status == "risk" else "info",
            payload={"score": result.score, "status": result.status, "previous_score": previous_score},
        )
    db.session.flush()
    return snapshot


def latest_operational_score(student: StudentProfile) -> dict:
    snapshot = (
        StudentHealthScore.query.filter_by(student_id=student.id, score_type="operational")
        .order_by(StudentHealthScore.computed_at.desc())
        .first()
    )
    if snapshot is None:
        snapshot = recompute_and_persist_score(student)
    components = snapshot.components_json or {}
    return {
        "score": snapshot.raw_score,
        "status": snapshot.level,
        "trend": snapshot.trend,
        "riskLevel": components.get("risk_level") or snapshot.level,
        "reason": components.get("reason") or "rotina recente sem alerta importante",
        "factors": components.get("factors") or {},
        "createdAt": snapshot.computed_at.isoformat(),
    }


def evaluate_retention_automation(student: StudentProfile) -> AutomationDecision | None:
    score = latest_operational_score(student)
    now = utcnow()
    active_recent = AutomationDecision.query.filter(
        AutomationDecision.student_id == student.id,
        AutomationDecision.rule_type.in_(["reengagement_light", "reengagement_medium", "reengagement_critical"]),
        AutomationDecision.created_at >= now - timedelta(days=2),
    ).first()
    if active_recent is not None:
        return active_recent

    risk = score["riskLevel"]
    if risk == "critical":
        rule_type = "reengagement_critical"
        priority = "high"
        action = "Enviar mensagem humana e abrir follow-up hoje"
        reason = "risco alto de abandono: " + score["reason"]
        cooldown_hours = 36
    elif risk == "high":
        rule_type = "reengagement_medium"
        priority = "high"
        action = "Sugerir ajuste de rotina e mensagem curta"
        reason = "aluno esfriando: " + score["reason"]
        cooldown_hours = 48
    elif risk == "medium":
        rule_type = "reengagement_light"
        priority = "medium"
        action = "Fazer check-in leve"
        reason = "atenção operacional: " + score["reason"]
        cooldown_hours = 72
    else:
        return None

    decision = AutomationDecision(
        account_id=student.account_id,
        student_id=student.id,
        rule_type=rule_type,
        status="suggested",
        priority=priority,
        reason=reason,
        suggested_action=action,
        suppressed_until=now + timedelta(hours=cooldown_hours),
        payload_json={"score": score},
    )
    db.session.add(decision)
    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type="automation_suggested",
        source="automation_engine",
        title=action,
        body=reason,
        severity="warning" if priority == "medium" else "critical",
        payload={"rule_type": rule_type, "cooldown_hours": cooldown_hours},
    )
    db.session.flush()
    return decision


def build_student_timeline(student: StudentProfile, *, limit: int = 30) -> list[dict]:
    items: list[dict] = []

    events = (
        StudentEvent.query.filter_by(student_id=student.id)
        .order_by(desc(StudentEvent.occurred_at))
        .limit(limit)
        .all()
    )
    items.extend(
        {
            "id": str(item.id),
            "kind": item.event_type,
            "source": item.source,
            "label": item.title,
            "body": item.body,
            "when": item.occurred_at.isoformat(),
            "severity": (item.payload_json or {}).get("severity", "info"),
        }
        for item in events
    )

    signals = (
        StudentDailySignal.query.filter_by(student_id=student.id)
        .order_by(desc(StudentDailySignal.created_at))
        .limit(limit)
        .all()
    )
    items.extend(
        {
            "id": str(item.id),
            "kind": item.signal_type,
            "source": item.source,
            "label": item.title,
            "body": item.body,
            "when": item.created_at.isoformat(),
            "severity": "warning" if item.signal_type in {"absence", "manual_note"} else "info",
        }
        for item in signals
    )

    interactions = (
        StudentInteraction.query.filter_by(student_id=student.id)
        .order_by(desc(StudentInteraction.interaction_at))
        .limit(limit)
        .all()
    )
    items.extend(
        {
            "id": str(item.id),
            "kind": item.interaction_type,
            "source": item.channel,
            "label": item.title,
            "body": item.body,
            "when": item.interaction_at.isoformat(),
            "severity": "info",
        }
        for item in interactions
    )

    insights = (
        AIInsight.query.filter_by(student_id=student.id)
        .order_by(desc(AIInsight.created_at))
        .limit(limit)
        .all()
    )
    items.extend(
        {
            "id": str(item.id),
            "kind": "insight",
            "source": "ai",
            "label": item.title,
            "body": item.body,
            "when": item.created_at.isoformat(),
            "severity": "warning" if item.priority in {"high", "urgent"} else "info",
        }
        for item in insights
    )

    messages = (
        SuggestedMessage.query.filter_by(student_id=student.id)
        .order_by(desc(SuggestedMessage.created_at))
        .limit(limit)
        .all()
    )
    items.extend(
        {
            "id": str(item.id),
            "kind": "suggested_message",
            "source": "ai",
            "label": item.subject_hint or item.message_category,
            "body": item.edited_message_text or item.message_text,
            "when": item.created_at.isoformat(),
            "severity": "info",
        }
        for item in messages
    )

    return sorted(items, key=lambda item: item["when"], reverse=True)[:limit]
