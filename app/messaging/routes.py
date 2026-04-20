from __future__ import annotations

from flask import Blueprint, request

from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth
from app.messaging.models import SuggestedMessage
from app.messaging.services import copy_message, dismiss_message, edit_message, require_message, serialize_message
from app.students.services import require_student


messaging_bp = Blueprint("messaging", __name__)


@messaging_bp.get("/students/<uuid:student_id>/suggested-messages")
@require_auth()
def list_messages(student_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    items = SuggestedMessage.query.filter_by(account_id=auth.account_id, student_id=student_id).order_by(SuggestedMessage.created_at.desc()).all()
    return success_response({"items": [serialize_message(item) for item in items]})


@messaging_bp.post("/suggested-messages/<uuid:message_id>/copy")
@require_auth({"owner", "professional", "admin"})
def copy_message_endpoint(message_id):
    auth = current_auth()
    message = require_message(auth.account_id, message_id)
    return success_response({"message": serialize_message(copy_message(message=message, actor_user_id=auth.user.id))})


@messaging_bp.post("/suggested-messages/<uuid:message_id>/edit")
@require_auth({"owner", "professional", "admin"})
def edit_message_endpoint(message_id):
    auth = current_auth()
    message = require_message(auth.account_id, message_id)
    edited_text = (request.get_json() or {}).get("edited_message_text", "")
    return success_response({"message": serialize_message(edit_message(message=message, actor_user_id=auth.user.id, edited_text=edited_text))})


@messaging_bp.post("/suggested-messages/<uuid:message_id>/dismiss")
@require_auth({"owner", "professional", "admin"})
def dismiss_message_endpoint(message_id):
    auth = current_auth()
    message = require_message(auth.account_id, message_id)
    return success_response({"message": serialize_message(dismiss_message(message=message))})
