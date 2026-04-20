from __future__ import annotations

from flask import Blueprint, request

from app.ai.local_agent import fitcopilot_agent
from app.common.api import success_response
from app.common.security.auth import require_auth


ai_bp = Blueprint("ai", __name__)


@ai_bp.post("/ai/overview")
@require_auth()
def ai_overview():
    payload = request.get_json() or {}
    result = fitcopilot_agent.process_request(task_type="WORKSPACE_OVERVIEW", payload=payload)
    return success_response({"status": result.status, "model": result.model, "result": result.result})


@ai_bp.post("/ai/student-day")
@require_auth()
def ai_student_day():
    payload = request.get_json() or {}
    result = fitcopilot_agent.process_request(task_type="STUDENT_DAILY_READING", payload=payload)
    return success_response({"status": result.status, "model": result.model, "result": result.result})


@ai_bp.post("/ai/message-suggestion")
@require_auth()
def ai_message_suggestion():
    payload = request.get_json() or {}
    result = fitcopilot_agent.process_request(task_type="MESSAGE_SUGGESTION", payload=payload)
    return success_response({"status": result.status, "model": result.model, "result": result.result})


@ai_bp.post("/ai/file-summary")
@require_auth()
def ai_file_summary():
    payload = request.get_json() or {}
    result = fitcopilot_agent.process_request(task_type="FILE_SUMMARY", payload=payload)
    return success_response({"status": result.status, "model": result.model, "result": result.result})


@ai_bp.post("/ai/progress-report")
@require_auth()
def ai_progress_report():
    payload = request.get_json() or {}
    result = fitcopilot_agent.process_request(task_type="PROGRESS_REPORT", payload=payload)
    return success_response({"status": result.status, "model": result.model, "result": result.result})
