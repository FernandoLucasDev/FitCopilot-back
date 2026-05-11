from __future__ import annotations

from http import HTTPStatus

from flask import Blueprint, current_app, request

from app.ai.bot_orchestrator import reply_for_whatsapp
from app.ai.local_agent import fitcopilot_agent
from app.common.api import ApiError, success_response
from app.common.security.auth import require_auth
from app.common.security.rate_limit import check_rate_limit, client_ip


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


@ai_bp.post("/internal/bot/whatsapp/respond")
def bot_whatsapp_respond():
    check_rate_limit(
        key=f"internal-bot:{client_ip()}",
        limit=int(current_app.config.get("BOT_RATE_LIMIT_PER_MINUTE", 60)),
        window_seconds=60,
    )
    bot_secret = request.headers.get("X-Bot-Secret")
    expected_secret = current_app.config.get("BOT_INTERNAL_SECRET")
    if not bot_secret or bot_secret != expected_secret:
        raise ApiError("Acesso interno invalido.", HTTPStatus.UNAUTHORIZED)

    payload = request.get_json() or {}
    reply = reply_for_whatsapp(
        phone_number=payload.get("phoneNumber"),
        text=payload.get("text"),
        message_type=payload.get("messageType", "text"),
        state_phase=payload.get("phase", "idle"),
        metadata=payload.get("metadata") or {},
    )
    return success_response(
        {
            "handled": reply.handled,
            "replyText": reply.reply_text,
            "nextPhase": reply.next_phase,
            "metadataPatch": reply.metadata_patch or {},
            "studentId": reply.student_id,
            "studentName": reply.student_name,
        }
    )
