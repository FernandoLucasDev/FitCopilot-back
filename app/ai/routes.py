from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from http import HTTPStatus
from uuid import UUID

from flask import Blueprint, current_app, request

from app.ai.bot_orchestrator import reply_for_whatsapp
from app.ai.local_agent import fitcopilot_agent
from app.common.api import ApiError, success_response
from app.common.security.auth import require_auth
from app.common.security.rate_limit import check_rate_limit, client_ip
from app.extensions import db
from app.auth.core_auth_service import core_auth_service
from app.integrations.core_email import core_email_gateway
from app.jobs.models import AuditLog
from app.auth.models import User
from app.whatsapp.services import apply_core_delivery_status, observe_core_inbound_message


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
    metadata = payload.get("metadata") or {}
    _, duplicate = observe_core_inbound_message(
        phone_number=payload.get("phoneNumber"),
        text=payload.get("text"),
        message_type=payload.get("messageType", "text"),
        metadata=metadata,
    )
    if duplicate:
        return success_response(
            {
                "handled": True,
                "replyText": "",
                "nextPhase": payload.get("phase", "idle"),
                "metadataPatch": {},
                "duplicate": True,
            }
        )
    media_safety = metadata.get("mediaSafety") or {}
    if media_safety and not bool(media_safety.get("allowed")):
        return success_response(
            {
                "handled": True,
                "replyText": str(media_safety.get("userMessage") or "Não consigo analisar essa imagem por aqui. Pode me enviar uma descrição em texto?"),
                "nextPhase": payload.get("phase", "idle"),
                "metadataPatch": {},
                "studentId": None,
                "studentName": None,
                "mediaBlocked": True,
            }
        )
    reply = reply_for_whatsapp(
        phone_number=payload.get("phoneNumber"),
        text=payload.get("text"),
        message_type=payload.get("messageType", "text"),
        state_phase=payload.get("phase", "idle"),
        metadata=metadata,
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


@ai_bp.post("/internal/bot/whatsapp/status")
def bot_whatsapp_status():
    check_rate_limit(
        key=f"internal-bot-status:{client_ip()}",
        limit=240,
        window_seconds=60,
    )
    bot_secret = request.headers.get("X-Bot-Secret")
    expected_secret = current_app.config.get("BOT_INTERNAL_SECRET")
    if not bot_secret or bot_secret != expected_secret:
        raise ApiError("Acesso interno invalido.", HTTPStatus.UNAUTHORIZED)

    payload = request.get_json() or {}
    dispatch, changed = apply_core_delivery_status(payload)
    if dispatch.local_status == "failed":
        _capture_whatsapp_delivery_failure_sentry(payload=payload, dispatch_id=str(dispatch.id))
    return success_response(
        {
            "dispatchId": str(dispatch.id),
            "status": dispatch.local_status,
            "changed": changed,
        }
    )


@ai_bp.post("/internal/bot/whatsapp/media-safety")
def bot_whatsapp_media_safety():
    check_rate_limit(
        key=f"internal-bot-media-safety:{client_ip()}",
        limit=60,
        window_seconds=60,
    )
    bot_secret = request.headers.get("X-Bot-Secret")
    expected_secret = current_app.config.get("BOT_INTERNAL_SECRET")
    if not bot_secret or bot_secret != expected_secret:
        raise ApiError("Acesso interno invalido.", HTTPStatus.UNAUTHORIZED)

    payload = request.get_json() or {}
    media = payload.get("media") or {}
    raw_base64 = str(media.get("base64") or "")
    mime_type = str(media.get("mimeType") or "image/jpeg")
    content = _decode_media_base64(raw_base64)
    if not content:
        current_app.logger.warning(
            "whatsapp_media_safety_blocked reason=invalid_media phone=%s message_id=%s",
            payload.get("phoneNumber"),
            payload.get("messageId"),
        )
        return success_response(
            {
                "allowed": False,
                "category": "media_unavailable",
                "severity": "block",
                "userMessage": "Não consegui validar essa imagem com segurança. Me descreve em texto que eu registro por aqui.",
                "confidence": 0,
            }
        )

    if len(content) > 5 * 1024 * 1024:
        return success_response(
            {
                "allowed": False,
                "category": "media_too_large",
                "severity": "block",
                "userMessage": "Essa imagem ficou pesada demais para validar com segurança. Me manda uma descrição em texto, por favor.",
                "confidence": 1,
            }
        )

    provider = current_app.extensions["ai_provider"]
    result = provider.moderate_media(
        content=content,
        mime_type=mime_type,
        context={
            "channel": "whatsapp",
            "phone_number": payload.get("phoneNumber"),
            "message_id": payload.get("messageId"),
            "message_type": payload.get("messageType"),
            "caption": payload.get("caption"),
        },
    )
    if not result.allowed:
        current_app.logger.warning(
            "whatsapp_media_safety_blocked phone=%s message_id=%s category=%s severity=%s confidence=%s",
            payload.get("phoneNumber"),
            payload.get("messageId"),
            result.category,
            result.severity,
            result.confidence,
        )
        _capture_media_safety_sentry(
            phone_number=payload.get("phoneNumber"),
            message_id=payload.get("messageId"),
            category=result.category,
            severity=result.severity,
        )

    return success_response(
        {
            "allowed": result.allowed,
            "category": result.category,
            "severity": result.severity,
            "userMessage": result.user_message,
            "confidence": result.confidence,
        }
    )


@ai_bp.post("/internal/bot/whatsapp/connection-alert")
def bot_whatsapp_connection_alert():
    check_rate_limit(
        key=f"internal-bot-connection:{client_ip()}",
        limit=20,
        window_seconds=60,
    )
    bot_secret = request.headers.get("X-Bot-Secret")
    expected_secret = current_app.config.get("BOT_INTERNAL_SECRET")
    if not bot_secret or bot_secret != expected_secret:
        raise ApiError("Acesso interno invalido.", HTTPStatus.UNAUTHORIZED)

    payload = request.get_json() or {}
    status = str(payload.get("status") or "unknown").lower()
    instance_name = str(payload.get("instanceName") or "whatsapp")
    reason = payload.get("reason")
    raw = payload.get("raw") or {}

    current_app.logger.error(
        "whatsapp_connection_lost instance=%s status=%s reason=%s raw=%s",
        instance_name,
        status,
        reason,
        raw,
    )
    _capture_whatsapp_connection_sentry(instance_name=instance_name, status=status, reason=reason)

    audit = AuditLog(
        account_id=None,
        actor_user_id=None,
        entity_type="whatsapp_session",
        entity_id=UUID(int=0),
        action="connection_lost",
        old_values_json=None,
        new_values_json={
            "instanceName": instance_name,
            "status": status,
            "reason": reason,
            "raw": raw,
        },
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(audit)
    db.session.commit()

    email_results = _notify_whatsapp_connection_owners(
        instance_name=instance_name,
        status=status,
        reason=reason,
    )
    return success_response({"notified": True, "emailResults": email_results})


def _capture_whatsapp_connection_sentry(*, instance_name: str, status: str, reason) -> None:
    try:
        import sentry_sdk

        with sentry_sdk.configure_scope() as scope:
            scope.set_context(
                "whatsapp_connection",
                {"instanceName": instance_name, "status": status, "reason": reason},
            )
            sentry_sdk.capture_message(f"WhatsApp session lost: {instance_name} ({status})", level="error")
    except Exception as exc:  # pragma: no cover - observability must never break the alert route.
        current_app.logger.warning("whatsapp_connection_sentry_capture_failed error=%s", exc)


def _capture_whatsapp_delivery_failure_sentry(*, payload: dict, dispatch_id: str) -> None:
    current_app.logger.error(
        "whatsapp_delivery_failed dispatch_id=%s core_message_id=%s code=%s error=%s",
        dispatch_id,
        payload.get("coreMessagePublicId"),
        payload.get("providerErrorCode"),
        payload.get("providerErrorMessage"),
    )
    try:
        import sentry_sdk

        with sentry_sdk.configure_scope() as scope:
            scope.set_context(
                "whatsapp_delivery",
                {
                    "dispatchId": dispatch_id,
                    "coreMessagePublicId": payload.get("coreMessagePublicId"),
                    "providerErrorCode": payload.get("providerErrorCode"),
                    "providerErrorMessage": payload.get("providerErrorMessage"),
                },
            )
            sentry_sdk.capture_message("WhatsApp delivery failed", level="error")
    except Exception as exc:  # pragma: no cover
        current_app.logger.warning("whatsapp_delivery_sentry_capture_failed error=%s", exc)


def _capture_media_safety_sentry(*, phone_number, message_id, category: str, severity: str) -> None:
    if severity not in {"critical", "block"}:
        return
    try:
        import sentry_sdk

        with sentry_sdk.configure_scope() as scope:
            scope.set_context(
                "whatsapp_media_safety",
                {
                    "phoneNumber": phone_number,
                    "messageId": message_id,
                    "category": category,
                    "severity": severity,
                },
            )
            sentry_sdk.capture_message(f"WhatsApp media blocked: {category}", level="warning")
    except Exception as exc:  # pragma: no cover
        current_app.logger.warning("whatsapp_media_safety_sentry_failed error=%s", exc)


def _decode_media_base64(raw_base64: str) -> bytes | None:
    if not raw_base64:
        return None
    if "," in raw_base64 and raw_base64.startswith("data:"):
        raw_base64 = raw_base64.split(",", 1)[1]
    try:
        return base64.b64decode(raw_base64, validate=True)
    except (binascii.Error, ValueError):
        return None


def _notify_whatsapp_connection_owners(*, instance_name: str, status: str, reason) -> list[dict]:
    owners = (
        User.query.filter(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.role.in_(["owner", "admin"]),
        )
        .order_by(User.created_at.asc())
        .limit(10)
        .all()
    )
    results = []
    for owner in owners:
        if not owner.core_access_token:
            results.append({"email": owner.email, "status": "skipped", "reason": "missing_core_access_token"})
            continue

        try:
            _send_whatsapp_connection_owner_email(
                owner=owner,
                instance_name=instance_name,
                status=status,
                reason=reason,
            )
            results.append({"email": owner.email, "status": "sent"})
        except Exception as exc:
            current_app.logger.warning(
                "whatsapp_connection_email_failed email=%s error=%s",
                owner.email,
                exc,
            )
            results.append({"email": owner.email, "status": "failed", "reason": str(exc)})
    return results


def _send_whatsapp_connection_owner_email(*, owner: User, instance_name: str, status: str, reason) -> None:
    html_content = _whatsapp_connection_email_html(
        owner_name=owner.full_name,
        instance_name=instance_name,
        status=status,
        reason=reason,
    )
    try:
        core_email_gateway.send_html_email(
            access_token=owner.core_access_token,
            to_email=owner.email,
            subject="WhatsApp do FitCopilot desconectado",
            html_content=html_content,
        )
        return
    except Exception as exc:
        response = getattr(exc, "response", None)
        if getattr(response, "status_code", None) != 401 or not owner.core_refresh_token:
            raise

    refreshed = core_auth_service.refresh(refresh_token=owner.core_refresh_token)
    owner.core_access_token = refreshed.get("access") or refreshed.get("access_token") or owner.core_access_token
    owner.core_refresh_token = refreshed.get("refresh") or refreshed.get("refresh_token") or owner.core_refresh_token
    db.session.add(owner)
    db.session.commit()
    core_email_gateway.send_html_email(
        access_token=owner.core_access_token,
        to_email=owner.email,
        subject="WhatsApp do FitCopilot desconectado",
        html_content=html_content,
    )


def _whatsapp_connection_email_html(*, owner_name: str, instance_name: str, status: str, reason) -> str:
    safe_reason = reason or "sem motivo detalhado informado pela Evolution API"
    return f"""
    <div style="font-family:Arial,sans-serif;color:#1f1a17;line-height:1.5">
      <h2>WhatsApp desconectado no FitCopilot</h2>
      <p>Oi, {owner_name}. A sessão do WhatsApp usada pelo Agente Fit saiu do ar.</p>
      <ul>
        <li><strong>Instância:</strong> {instance_name}</li>
        <li><strong>Status:</strong> {status}</li>
        <li><strong>Motivo:</strong> {safe_reason}</li>
      </ul>
      <p>Enquanto ela estiver desconectada, o bot não recebe mensagens dos alunos nem envia respostas automáticas.</p>
      <p>Abra o painel operacional do FitCopilot ou o endpoint interno de conexão para escanear um novo QR Code.</p>
    </div>
    """
