from __future__ import annotations

from flask import Blueprint

from app.common.api import success_response
from app.common.request import parse_json
from app.common.security.auth import current_auth, require_auth
from app.messaging.services import dismiss_message, edit_message, require_message
from app.students.services import require_student
from app.whatsapp.schemas import SendStudentWhatsAppMessageInput, SimulateInboundWhatsAppInput, StudentAutomationConfigInput
from app.whatsapp.services import (
    account_whatsapp_status,
    list_student_whatsapp_suggestions,
    list_whatsapp_history,
    record_inbound_message,
    send_daily_checkin,
    send_manual_whatsapp_message,
    send_onboarding_message,
    send_suggested_message,
    send_workout_of_day,
    student_whatsapp_status,
    update_student_automations,
)


whatsapp_bp = Blueprint("whatsapp", __name__)


@whatsapp_bp.get("/whatsapp/status")
@require_auth({"owner", "professional", "admin"})
def whatsapp_status():
    auth = current_auth()
    return success_response(account_whatsapp_status(auth.account_id))


@whatsapp_bp.get("/whatsapp/students/<uuid:student_id>/status")
@require_auth({"owner", "professional", "admin"})
def student_whatsapp_status_endpoint(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    return success_response(student_whatsapp_status(student))


@whatsapp_bp.post("/students/<uuid:student_id>/whatsapp/onboard")
@require_auth({"owner", "professional", "admin"})
def onboard_student_whatsapp(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    dispatch = send_onboarding_message(student=student, actor_user_id=auth.user.id)
    return success_response({"dispatch": {"id": str(dispatch.id), "status": dispatch.local_status}}, 202)


@whatsapp_bp.post("/students/<uuid:student_id>/whatsapp/send-checkin")
@require_auth({"owner", "professional", "admin"})
def send_student_checkin(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    dispatch = send_daily_checkin(student=student, actor_user_id=auth.user.id)
    return success_response({"dispatch": {"id": str(dispatch.id), "status": dispatch.local_status}}, 202)


@whatsapp_bp.post("/students/<uuid:student_id>/whatsapp/send-workout")
@require_auth({"owner", "professional", "admin"})
def send_student_workout(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    dispatch = send_workout_of_day(student=student, actor_user_id=auth.user.id)
    return success_response({"dispatch": {"id": str(dispatch.id), "status": dispatch.local_status}}, 202)


@whatsapp_bp.post("/students/<uuid:student_id>/whatsapp/send-message")
@require_auth({"owner", "professional", "admin"})
def send_student_message(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    payload = parse_json(SendStudentWhatsAppMessageInput)
    dispatch = send_manual_whatsapp_message(
        student=student,
        actor_user_id=auth.user.id,
        message_text=payload.message_text,
        message_type=payload.message_type,
    )
    return success_response({"dispatch": {"id": str(dispatch.id), "status": dispatch.local_status}}, 202)


@whatsapp_bp.get("/students/<uuid:student_id>/whatsapp/history")
@require_auth({"owner", "professional", "admin"})
def student_whatsapp_history(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    return success_response(list_whatsapp_history(student))


@whatsapp_bp.get("/students/<uuid:student_id>/whatsapp/suggestions")
@require_auth({"owner", "professional", "admin"})
def student_whatsapp_suggestions(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    return success_response({"items": list_student_whatsapp_suggestions(student)})


@whatsapp_bp.post("/students/<uuid:student_id>/whatsapp/suggestions/<uuid:suggestion_id>/send")
@require_auth({"owner", "professional", "admin"})
def send_student_whatsapp_suggestion(student_id, suggestion_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    suggestion = require_message(auth.account_id, suggestion_id)
    dispatch = send_suggested_message(student=student, actor_user_id=auth.user.id, suggestion=suggestion)
    return success_response({"dispatch": {"id": str(dispatch.id), "status": dispatch.local_status}}, 202)


@whatsapp_bp.post("/students/<uuid:student_id>/whatsapp/suggestions/<uuid:suggestion_id>/edit")
@require_auth({"owner", "professional", "admin"})
def edit_student_whatsapp_suggestion(student_id, suggestion_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    suggestion = require_message(auth.account_id, suggestion_id)
    payload = parse_json(SendStudentWhatsAppMessageInput)
    edited = edit_message(message=suggestion, actor_user_id=auth.user.id, edited_text=payload.message_text)
    return success_response({"suggestion": {"id": str(edited.id), "status": edited.status}})


@whatsapp_bp.post("/students/<uuid:student_id>/whatsapp/suggestions/<uuid:suggestion_id>/dismiss")
@require_auth({"owner", "professional", "admin"})
def dismiss_student_whatsapp_suggestion(student_id, suggestion_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    suggestion = require_message(auth.account_id, suggestion_id)
    dismissed = dismiss_message(message=suggestion)
    return success_response({"suggestion": {"id": str(dismissed.id), "status": dismissed.status}})


@whatsapp_bp.get("/students/<uuid:student_id>/whatsapp/automations")
@require_auth({"owner", "professional", "admin"})
def student_whatsapp_automations(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    return success_response({"items": student_whatsapp_status(student)["automations"]})


@whatsapp_bp.patch("/students/<uuid:student_id>/whatsapp/automations")
@require_auth({"owner", "professional", "admin"})
def patch_student_whatsapp_automations(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    payload = parse_json(StudentAutomationConfigInput)
    return success_response({"items": update_student_automations(student=student, actor_user_id=auth.user.id, data=payload)})


@whatsapp_bp.post("/students/<uuid:student_id>/whatsapp/inbound")
@require_auth({"owner", "professional", "admin"})
def simulate_student_whatsapp_inbound(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    payload = parse_json(SimulateInboundWhatsAppInput)
    inbound = record_inbound_message(
        student=student,
        phone=payload.phone,
        message_type=payload.message_type,
        text_body=payload.text_body,
        media_json=payload.media_json,
        raw_payload_json=payload.raw_payload_json,
    )
    return success_response({"inbound": {"id": str(inbound.id), "status": inbound.processing_status}}, 202)
