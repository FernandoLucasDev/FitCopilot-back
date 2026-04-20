from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus

from app.common.api import ApiError
from app.extensions import db
from app.jobs.services import create_audit_log
from app.messaging.models import SuggestedMessage
from app.students.models import StudentInteraction


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require_message(account_id, message_id) -> SuggestedMessage:
    item = SuggestedMessage.query.filter_by(id=message_id, account_id=account_id).first()
    if item is None:
        raise ApiError("Mensagem sugerida não encontrada", HTTPStatus.NOT_FOUND)
    return item


def copy_message(*, message: SuggestedMessage, actor_user_id):
    message.status = "copied"
    message.acted_at = utcnow()
    interaction = StudentInteraction(
        account_id=message.account_id,
        student_id=message.student_id,
        interaction_type="outgoing_message",
        channel="manual",
        title="Mensagem copiada para envio",
        body=message.edited_message_text or message.message_text,
        related_message_id=message.id,
        created_by_user_id=actor_user_id,
        interaction_at=utcnow(),
        created_at=utcnow(),
    )
    db.session.add(interaction)
    db.session.commit()
    return message


def edit_message(*, message: SuggestedMessage, actor_user_id, edited_text: str):
    message.edited_message_text = edited_text
    message.status = "edited"
    message.acted_at = utcnow()
    create_audit_log(
        account_id=message.account_id,
        actor_user_id=actor_user_id,
        entity_type="suggested_message",
        entity_id=message.id,
        action="edited",
        new_values={"status": message.status},
    )
    db.session.commit()
    return message


def dismiss_message(*, message: SuggestedMessage):
    message.status = "dismissed"
    message.acted_at = utcnow()
    db.session.commit()
    return message


def serialize_message(item: SuggestedMessage) -> dict:
    return {
        "id": str(item.id),
        "category": item.message_category,
        "messageText": item.message_text,
        "editedMessageText": item.edited_message_text,
        "status": item.status,
        "subjectHint": item.subject_hint,
        "tone": item.tone,
    }
