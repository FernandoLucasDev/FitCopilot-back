from __future__ import annotations

from flask import Blueprint, current_app, redirect, request

from app.common.api import ApiError, success_response
from app.students.portal_services import require_student_session
from app.wearables.services import (
    complete_wearable_connect,
    disconnect_wearable,
    serialize_wearable_summary,
    start_wearable_connect,
)

wearables_bp = Blueprint("wearables", __name__)


@wearables_bp.post("/student-portal/wearable/connect")
def student_wearable_connect():
    student = require_student_session()
    provider = (request.get_json(silent=True) or {}).get("provider", "strava")
    return success_response(start_wearable_connect(student=student, provider=provider))


@wearables_bp.post("/student-portal/wearable/disconnect")
def student_wearable_disconnect():
    student = require_student_session()
    provider = (request.get_json(silent=True) or {}).get("provider", "strava")
    return success_response(disconnect_wearable(student=student, source=provider))


@wearables_bp.get("/student-portal/wearable/summary")
def student_wearable_summary():
    student = require_student_session()
    return success_response(serialize_wearable_summary(student))


@wearables_bp.get("/wearable/<provider>/callback")
def wearable_oauth_callback(provider: str):
    code = request.args.get("code")
    state = request.args.get("state")
    portal_url = current_app.config.get("STUDENT_PORTAL_URL", "/aluno")
    if not code or not state:
        return redirect(f"{portal_url}?wearable=error")
    try:
        complete_wearable_connect(code=code, state=state)
    except ApiError:
        return redirect(f"{portal_url}?wearable=error")
    return redirect(f"{portal_url}?wearable=connected")
