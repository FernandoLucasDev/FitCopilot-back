from __future__ import annotations

from flask import Blueprint

from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth
from app.insights.models import AIInsight
from app.insights.services import apply_insight, dismiss_insight, require_insight, serialize_insight
from app.students.services import require_student


insights_bp = Blueprint("insights", __name__)


@insights_bp.get("/students/<uuid:student_id>/insights")
@require_auth()
def list_insights(student_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    items = AIInsight.query.filter_by(account_id=auth.account_id, student_id=student_id).order_by(AIInsight.created_at.desc()).all()
    return success_response({"items": [serialize_insight(item) for item in items]})


@insights_bp.post("/insights/<uuid:insight_id>/apply")
@require_auth({"owner", "professional", "admin"})
def apply_insight_endpoint(insight_id):
    auth = current_auth()
    insight = require_insight(auth.account_id, insight_id)
    return success_response({"insight": serialize_insight(apply_insight(insight=insight, actor_user_id=auth.user.id))})


@insights_bp.post("/insights/<uuid:insight_id>/dismiss")
@require_auth({"owner", "professional", "admin"})
def dismiss_insight_endpoint(insight_id):
    auth = current_auth()
    insight = require_insight(auth.account_id, insight_id)
    return success_response({"insight": serialize_insight(dismiss_insight(insight=insight, actor_user_id=auth.user.id))})
