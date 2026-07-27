from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus

from app.common.api import ApiError
from app.extensions import db
from app.insights.models import AIInsight
from app.jobs.services import create_audit_log
from app.students.models import StudentInteraction


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require_insight(account_id, insight_id) -> AIInsight:
    item = AIInsight.query.filter_by(id=insight_id, account_id=account_id).first()
    if item is None:
        raise ApiError("Insight não encontrado", HTTPStatus.NOT_FOUND)
    return item


def apply_insight(*, insight: AIInsight, actor_user_id):
    insight.status = "applied"
    insight.applied_at = utcnow()
    interaction = StudentInteraction(
        account_id=insight.account_id,
        student_id=insight.student_id,
        interaction_type="recommendation",
        channel="system",
        title=insight.title,
        body=insight.body,
        created_by_user_id=actor_user_id,
        interaction_at=utcnow(),
        created_at=utcnow(),
    )
    db.session.add(interaction)
    create_audit_log(
        account_id=insight.account_id,
        actor_user_id=actor_user_id,
        entity_type="ai_insight",
        entity_id=insight.id,
        action="applied",
        new_values={"status": insight.status},
    )
    db.session.commit()
    return insight


def dismiss_insight(*, insight: AIInsight, actor_user_id):
    insight.status = "dismissed"
    create_audit_log(
        account_id=insight.account_id,
        actor_user_id=actor_user_id,
        entity_type="ai_insight",
        entity_id=insight.id,
        action="dismissed",
        new_values={"status": insight.status},
    )
    db.session.commit()
    return insight


def build_student_ai_context(student) -> dict:
    """Contexto compacto pros feedbacks sob demanda (recap/perguntas).
    Defensivo: cada bloco que falhar e simplesmente omitido."""
    context: dict = {"student_name": student.full_name, "goal": getattr(student, "goal", None)}
    try:
        from app.operations.services import latest_operational_score

        op = latest_operational_score(student)
        context["score"] = op.get("score")
        context["score_trend"] = op.get("trend")
        context["score_reason"] = op.get("reason")
    except Exception:
        pass
    try:
        from app.workouts.services import summarize_workout_consistency

        consistency = summarize_workout_consistency(student) or {}
        context["workouts_completed"] = consistency.get("completedCount")
        context["workouts_skipped"] = consistency.get("skippedCount")
        context["consistency_summary"] = consistency.get("summary")
    except Exception:
        pass
    try:
        from app.students.services import get_recent_signals

        context["recent_signals"] = [
            {"kind": signal.signal_type, "title": signal.title, "when": signal.created_at.isoformat()}
            for signal in get_recent_signals(student.id)[:8]
        ]
    except Exception:
        pass
    try:
        if getattr(student.account, "professional_vertical", None) == "nutricionista":
            from app.nutrition.services import weekly_food_summary

            food = weekly_food_summary(student)
            if isinstance(food, dict):
                context["nutrition"] = {
                    "daysWithLog": food.get("daysWithLog"),
                    "avgCaloriesKcal": food.get("avgCaloriesKcal"),
                    "targetAdherencePct": food.get("targetAdherencePct"),
                }
    except Exception:
        pass
    try:
        from app.insights.coach_signals import build_coach_signals

        context["coach_signals"] = [
            {"title": item["title"], "detail": item["detail"], "severity": item["severity"]}
            for item in build_coach_signals(student)
        ]
    except Exception:
        pass
    return context


def serialize_insight(item: AIInsight) -> dict:
    return {
        "id": str(item.id),
        "scope": item.insight_scope,
        "type": item.insight_type,
        "title": item.title,
        "body": item.body,
        "priority": item.priority,
        "status": item.status,
        "actionLabel": item.action_label,
    }
