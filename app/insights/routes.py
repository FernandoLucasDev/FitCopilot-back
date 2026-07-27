from __future__ import annotations

from flask import Blueprint, current_app

from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth
from app.insights.models import AIInsight
from app.insights.services import (
    apply_insight,
    build_student_ai_context,
    dismiss_insight,
    require_insight,
    serialize_insight,
)
from app.students.services import require_student
from app.whatsapp.services import list_whatsapp_history


insights_bp = Blueprint("insights", __name__)


@insights_bp.post("/students/<uuid:student_id>/insights/weekly-recap")
@require_auth({"owner", "professional", "admin"})
def weekly_recap_endpoint(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    provider = current_app.extensions["ai_provider"]
    recap = provider.weekly_recap(context=build_student_ai_context(student))
    return success_response({"recap": recap})


@insights_bp.post("/students/<uuid:student_id>/insights/questions")
@require_auth({"owner", "professional", "admin"})
def student_questions_endpoint(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    provider = current_app.extensions["ai_provider"]
    questions = provider.student_questions(context=build_student_ai_context(student))
    return success_response({"questions": questions})


@insights_bp.post("/students/<uuid:student_id>/insights/sentiment")
@require_auth({"owner", "professional", "admin"})
def student_sentiment_endpoint(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    provider = current_app.extensions["ai_provider"]
    history = list_whatsapp_history(student)
    inbound = history.get("inbound", []) if isinstance(history, dict) else []
    messages = [item.get("textBody") for item in inbound if item.get("textBody")]
    result = provider.analyze_sentiment(messages=messages, context={"student_name": student.full_name})
    # Privacidade: nada e persistido — devolvemos apenas o rotulo + a nota curta.
    return success_response({"label": result.label, "note": result.note, "confidence": result.confidence})


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
