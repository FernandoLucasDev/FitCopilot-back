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
