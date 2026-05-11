from __future__ import annotations

import re
from datetime import date, datetime, timezone
from http import HTTPStatus
from uuid import uuid4

from flask import current_app

from app.common.api import ApiError
from app.extensions import db
from app.integrations.core_messaging_client import core_messaging_client
from app.jobs.services import create_audit_log, create_background_job
from app.messaging.models import SuggestedMessage
from app.operations.services import emit_event, evaluate_retention_automation, recompute_and_persist_score
from app.students.models import StudentDailySignal, StudentInteraction, StudentProfile
from app.students.services import require_student
from app.whatsapp.models import (
    InboundMessageRecord,
    OutboundMessageDispatch,
    WhatsAppAutomationRule,
    WhatsAppDeliveryStatusEvent,
    WhatsAppSession,
)
from app.workouts.services import get_active_workout_for_student, serialize_workout_plan


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    default_country = str(current_app.config.get("WHATSAPP_DEFAULT_COUNTRY_CODE", "55"))
    if digits.startswith(default_country):
        return digits
    if len(digits) in {10, 11}:
        return f"{default_country}{digits}"
    return digits


def _owner_context(student: StudentProfile) -> tuple[str, str | None]:
    owner = student.account.users[0] if student.account and student.account.users else None
    token = owner.core_access_token if owner else None
    org_id = student.account.external_org_id if student.account else None
    if not token:
        raise ApiError("Sessão CORE indisponível para envio de WhatsApp.", HTTPStatus.CONFLICT)
    return token, org_id


def _idempotency_key(prefix: str, student: StudentProfile, suffix: str) -> str:
    return f"{prefix}:{student.id}:{suffix}"


def _find_existing_dispatch(idempotency_key: str) -> OutboundMessageDispatch | None:
    return OutboundMessageDispatch.query.filter_by(idempotency_key=idempotency_key).first()


def _upsert_session(*, student: StudentProfile, flow: str, step: str, context: dict | None = None) -> WhatsAppSession:
    session = (
        WhatsAppSession.query.filter_by(student_id=student.id, status="active")
        .order_by(WhatsAppSession.updated_at.desc())
        .first()
    )
    if session is None:
        session = WhatsAppSession(
            account_id=student.account_id,
            student_id=student.id,
            channel="whatsapp",
            status="active",
            current_flow=flow,
            current_step=step,
            context_json=context or {},
            started_at=utcnow(),
        )
        db.session.add(session)
        db.session.flush()
        return session
    session.current_flow = flow
    session.current_step = step
    session.context_json = context or session.context_json or {}
    return session


def _dispatch_status_payload(item: OutboundMessageDispatch) -> dict:
    return {
        "id": str(item.id),
        "category": item.message_category,
        "status": item.local_status,
        "coreMessagePublicId": item.core_message_public_id,
        "externalReference": item.external_reference,
        "payload": item.payload_json,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }


def get_or_create_student_automations(student: StudentProfile) -> list[WhatsAppAutomationRule]:
    rules = WhatsAppAutomationRule.query.filter_by(account_id=student.account_id).all()
    if rules:
        return rules
    defaults = [
        WhatsAppAutomationRule(
            account_id=student.account_id,
            name="Check-in diário",
            rule_type="daily_checkin",
            is_active=True,
            schedule_json={"hour": current_app.config.get("WHATSAPP_CHECKIN_HOUR", 8)},
            filters_json={"student_id": str(student.id)},
            template_config_json={},
        ),
        WhatsAppAutomationRule(
            account_id=student.account_id,
            name="Lembrete de treino",
            rule_type="workout_delivery",
            is_active=True,
            schedule_json={"hour": current_app.config.get("WHATSAPP_CHECKIN_HOUR", 8)},
            filters_json={"student_id": str(student.id)},
            template_config_json={},
        ),
        WhatsAppAutomationRule(
            account_id=student.account_id,
            name="Reengajamento",
            rule_type="reengagement",
            is_active=True,
            schedule_json={"cooldown_days": 2},
            filters_json={"student_id": str(student.id)},
            template_config_json={},
        ),
    ]
    db.session.add_all(defaults)
    db.session.commit()
    return defaults


def update_student_automations(*, student: StudentProfile, actor_user_id, data) -> list[dict]:
    rules = {rule.rule_type: rule for rule in get_or_create_student_automations(student)}
    if data.daily_checkin_active is not None:
        rules["daily_checkin"].is_active = data.daily_checkin_active
    if data.daily_checkin_hour is not None:
        rules["daily_checkin"].schedule_json = {**(rules["daily_checkin"].schedule_json or {}), "hour": data.daily_checkin_hour}
    if data.reminder_active is not None:
        rules["workout_delivery"].is_active = data.reminder_active
    if data.reengagement_active is not None:
        rules["reengagement"].is_active = data.reengagement_active
    if data.preferred_window_start is not None or data.preferred_window_end is not None:
        filters = rules["daily_checkin"].filters_json or {}
        if data.preferred_window_start is not None:
            filters["preferred_window_start"] = data.preferred_window_start
        if data.preferred_window_end is not None:
            filters["preferred_window_end"] = data.preferred_window_end
        rules["daily_checkin"].filters_json = filters
    create_audit_log(
        account_id=student.account_id,
        actor_user_id=actor_user_id,
        entity_type="whatsapp_automations",
        entity_id=student.id,
        action="updated",
        new_values={"student_id": str(student.id)},
    )
    db.session.commit()
    return [serialize_automation(rule) for rule in rules.values()]


def serialize_automation(rule: WhatsAppAutomationRule) -> dict:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "ruleType": rule.rule_type,
        "isActive": rule.is_active,
        "schedule": rule.schedule_json,
        "filters": rule.filters_json,
    }


def student_whatsapp_status(student: StudentProfile) -> dict:
    phone = normalize_phone(student.phone)
    latest_dispatch = (
        OutboundMessageDispatch.query.filter_by(student_id=student.id)
        .order_by(OutboundMessageDispatch.created_at.desc())
        .first()
    )
    latest_inbound = (
        InboundMessageRecord.query.filter_by(student_id=student.id)
        .order_by(InboundMessageRecord.received_at.desc())
        .first()
    )
    active_session = (
        WhatsAppSession.query.filter_by(student_id=student.id, status="active")
        .order_by(WhatsAppSession.updated_at.desc())
        .first()
    )
    rules = [serialize_automation(rule) for rule in get_or_create_student_automations(student)]
    return {
        "studentId": str(student.id),
        "phone": phone,
        "channelStatus": "ready" if phone else "missing_phone",
        "lastOutbound": _dispatch_status_payload(latest_dispatch) if latest_dispatch else None,
        "lastInbound": serialize_inbound(latest_inbound) if latest_inbound else None,
        "activeFlow": active_session.current_flow if active_session else None,
        "activeStep": active_session.current_step if active_session else None,
        "automations": rules,
    }


def account_whatsapp_status(account_id) -> dict:
    students = StudentProfile.query.filter_by(account_id=account_id, archived_at=None).all()
    total_phone = len([student for student in students if normalize_phone(student.phone)])
    total_dispatches = OutboundMessageDispatch.query.filter_by(account_id=account_id).count()
    failed_dispatches = OutboundMessageDispatch.query.filter_by(account_id=account_id, local_status="failed").count()
    return {
        "studentsWithPhone": total_phone,
        "dispatchesCount": total_dispatches,
        "failedDispatches": failed_dispatches,
        "channel": "whatsapp",
    }


def list_whatsapp_history(student: StudentProfile) -> dict:
    outbound = (
        OutboundMessageDispatch.query.filter_by(student_id=student.id)
        .order_by(OutboundMessageDispatch.created_at.desc())
        .limit(30)
        .all()
    )
    inbound = (
        InboundMessageRecord.query.filter_by(student_id=student.id)
        .order_by(InboundMessageRecord.received_at.desc())
        .limit(30)
        .all()
    )
    return {
        "outbound": [_dispatch_status_payload(item) for item in outbound],
        "inbound": [serialize_inbound(item) for item in inbound],
    }


def serialize_inbound(item: InboundMessageRecord) -> dict:
    return {
        "id": str(item.id),
        "messageType": item.message_type,
        "textBody": item.text_body,
        "parsedIntent": item.parsed_intent,
        "confidence": item.confidence,
        "processingStatus": item.processing_status,
        "receivedAt": item.received_at.isoformat(),
    }


def list_student_whatsapp_suggestions(student: StudentProfile) -> list[dict]:
    items = (
        SuggestedMessage.query.filter_by(student_id=student.id)
        .order_by(SuggestedMessage.created_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "id": str(item.id),
            "category": item.message_category,
            "status": item.status,
            "subjectHint": item.subject_hint,
            "text": item.edited_message_text or item.message_text,
        }
        for item in items
    ]


def queue_whatsapp_dispatch(
    *,
    student: StudentProfile,
    actor_user_id,
    message_category: str,
    related_entity_type: str | None,
    related_entity_id,
    idempotency_key: str,
    external_reference: str,
    payload: dict,
    enqueue: bool = True,
) -> OutboundMessageDispatch:
    existing = _find_existing_dispatch(idempotency_key)
    if existing is not None:
        return existing
    dispatch = OutboundMessageDispatch(
        account_id=student.account_id,
        student_id=student.id,
        related_entity_type=related_entity_type,
        related_entity_id=str(related_entity_id) if related_entity_id else None,
        message_category=message_category,
        idempotency_key=idempotency_key,
        external_reference=external_reference,
        requested_by_service=current_app.config.get("WHATSAPP_REQUESTED_BY_SERVICE", "fitcopilot-backend"),
        local_status="queued",
        payload_json=payload,
    )
    db.session.add(dispatch)
    db.session.flush()
    create_audit_log(
        account_id=student.account_id,
        actor_user_id=actor_user_id,
        entity_type="whatsapp_dispatch",
        entity_id=dispatch.id,
        action="queued",
        new_values={"message_category": message_category, "external_reference": external_reference},
    )
    create_background_job(
        job_type="send_whatsapp_message_job",
        status="queued",
        payload={"dispatch_id": str(dispatch.id)},
        account_id=student.account_id,
        student_id=student.id,
        reference_type="whatsapp_dispatch",
        reference_id=dispatch.id,
    )
    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type="message_queued",
        source="whatsapp",
        title=f"WhatsApp enfileirado: {message_category}",
        body=payload.get("text", {}).get("body") or payload.get("interactive", {}).get("body"),
        event_key=f"whatsapp_dispatch_queued:{dispatch.id}",
        payload={"dispatch_id": str(dispatch.id), "category": message_category},
    )
    db.session.commit()
    if enqueue:
        from app.jobs.tasks import send_whatsapp_message_job

        send_whatsapp_message_job.delay(str(dispatch.id))
    else:
        perform_dispatch(str(dispatch.id))
    return dispatch


def _build_text_payload(*, body: str, message_type: str = "text", buttons: list[dict] | None = None, media: dict | None = None, template: dict | None = None) -> dict:
    payload = {"message_type": message_type}
    if message_type == "interactive":
        payload["interactive"] = {"body": body, "buttons": buttons or []}
    elif message_type == "media":
        payload["media"] = media or {}
    elif message_type == "template":
        payload["template"] = template or {}
    else:
        payload["text"] = {"body": body}
    return payload


def send_onboarding_message(*, student: StudentProfile, actor_user_id, enqueue: bool = True) -> OutboundMessageDispatch:
    phone = normalize_phone(student.phone)
    if not phone:
        raise ApiError("Aluno sem telefone válido para WhatsApp.", HTTPStatus.CONFLICT)
    body = (
        f"Oi, {student.full_name.split()[0]}! 👋 Seu acompanhamento no FitCopilot começou.\n\n"
        f"{student.primary_professional.user.full_name if student.primary_professional and student.primary_professional.user else 'Seu profissional'} "
        "vai acompanhar seus treinos, evolução e rotina por aqui.\n"
        "Quer começar hoje?"
    )
    session = _upsert_session(student=student, flow="onboarding", step="awaiting_confirmation", context={"source": "professional_trigger"})
    dispatch = queue_whatsapp_dispatch(
        student=student,
        actor_user_id=actor_user_id,
        message_category="onboarding",
        related_entity_type="whatsapp_session",
        related_entity_id=session.id,
        idempotency_key=_idempotency_key("onboarding", student, date.today().isoformat()),
        external_reference=f"student:{student.id}:onboarding:{date.today().isoformat()}",
        payload=_build_text_payload(
            body=body,
            message_type="interactive",
            buttons=[
                {"type": "reply", "id": "start_today", "title": "Começar hoje"},
                {"type": "reply", "id": "later_today", "title": "Mais tarde"},
            ],
        ),
        enqueue=enqueue,
    )
    return dispatch


def send_daily_checkin(*, student: StudentProfile, actor_user_id, enqueue: bool = True) -> OutboundMessageDispatch:
    phone = normalize_phone(student.phone)
    if not phone:
        raise ApiError("Aluno sem telefone válido para WhatsApp.", HTTPStatus.CONFLICT)
    existing_signal = StudentDailySignal.query.filter_by(student_id=student.id, signal_date=date.today(), signal_type="workout").first()
    if existing_signal:
        raise ApiError("Treino já registrado hoje. Check-in redundante bloqueado.", HTTPStatus.CONFLICT)
    session = _upsert_session(student=student, flow="daily_checkin", step="awaiting_intent")
    return queue_whatsapp_dispatch(
        student=student,
        actor_user_id=actor_user_id,
        message_category="daily_checkin",
        related_entity_type="whatsapp_session",
        related_entity_id=session.id,
        idempotency_key=_idempotency_key("daily-checkin", student, date.today().isoformat()),
        external_reference=f"student:{student.id}:daily_checkin:{date.today().isoformat()}",
        payload=_build_text_payload(
            body="Você vai treinar hoje? 💪",
            message_type="interactive",
            buttons=[
                {"type": "reply", "id": "checkin_yes", "title": "Sim"},
                {"type": "reply", "id": "checkin_no", "title": "Não"},
                {"type": "reply", "id": "checkin_later", "title": "Mais tarde"},
            ],
        ),
        enqueue=enqueue,
    )


def send_workout_of_day(*, student: StudentProfile, actor_user_id, enqueue: bool = True) -> OutboundMessageDispatch:
    plan = get_active_workout_for_student(student.id)
    if not plan:
        raise ApiError("Aluno sem ficha ativa para envio.", HTTPStatus.CONFLICT)
    serialized = serialize_workout_plan(plan)
    exercises_count = sum(len(day["exercises"]) for day in serialized["days"])
    body = (
        f"Seu treino de hoje: {plan.title} 💪\n\n"
        f"São {exercises_count} exercícios. "
        f"{plan.objective or 'Foque em boa execução.'}"
    )
    session = _upsert_session(student=student, flow="workout_execution", step="awaiting_start", context={"plan_id": str(plan.id)})
    return queue_whatsapp_dispatch(
        student=student,
        actor_user_id=actor_user_id,
        message_category="workout_delivery",
        related_entity_type="workout_plan",
        related_entity_id=plan.id,
        idempotency_key=_idempotency_key("workout-of-day", student, date.today().isoformat()),
        external_reference=f"student:{student.id}:workout_of_day:{date.today().isoformat()}",
        payload=_build_text_payload(
            body=body,
            message_type="interactive",
            buttons=[
                {"type": "reply", "id": "start_workout", "title": "Começar treino"},
                {"type": "reply", "id": "need_other", "title": "Outro treino"},
            ],
        ),
        enqueue=enqueue,
    )


def send_manual_whatsapp_message(*, student: StudentProfile, actor_user_id, message_text: str, message_type: str = "text", related_entity_type: str | None = None, related_entity_id=None, enqueue: bool = True) -> OutboundMessageDispatch:
    phone = normalize_phone(student.phone)
    if not phone:
        raise ApiError("Aluno sem telefone válido para WhatsApp.", HTTPStatus.CONFLICT)
    return queue_whatsapp_dispatch(
        student=student,
        actor_user_id=actor_user_id,
        message_category="manual_message",
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        idempotency_key=_idempotency_key("manual-message", student, str(uuid4())),
        external_reference=f"student:{student.id}:manual_message:{uuid4()}",
        payload=_build_text_payload(body=message_text, message_type=message_type),
        enqueue=enqueue,
    )


def send_suggested_message(*, student: StudentProfile, actor_user_id, suggestion: SuggestedMessage, enqueue: bool = True) -> OutboundMessageDispatch:
    suggestion.status = "queued"
    suggestion.acted_at = utcnow()
    dispatch = send_manual_whatsapp_message(
        student=student,
        actor_user_id=actor_user_id,
        message_text=suggestion.edited_message_text or suggestion.message_text,
        related_entity_type="suggested_message",
        related_entity_id=suggestion.id,
        enqueue=enqueue,
    )
    suggestion.status = "sent" if not enqueue else "queued"
    db.session.commit()
    return dispatch


def perform_dispatch(dispatch_id: str) -> OutboundMessageDispatch:
    dispatch = OutboundMessageDispatch.query.filter_by(id=dispatch_id).first()
    if dispatch is None:
        raise ApiError("Dispatch de WhatsApp não encontrado.", HTTPStatus.NOT_FOUND)
    student = require_student(dispatch.account_id, dispatch.student_id)
    phone = normalize_phone(student.phone)
    if not phone:
        dispatch.local_status = "failed"
        db.session.commit()
        raise ApiError("Aluno sem telefone válido.", HTTPStatus.CONFLICT)

    token, org_id = _owner_context(student)
    payload = dispatch.payload_json or {}
    message_type = payload.get("message_type", "text")
    if message_type == "interactive":
        response = core_messaging_client.send_interactive_message(
            token=token,
            to_phone=phone,
            body=payload.get("interactive", {}).get("body", ""),
            buttons=payload.get("interactive", {}).get("buttons", []),
            idempotency_key=dispatch.idempotency_key,
            external_reference=dispatch.external_reference,
            requested_by_service=dispatch.requested_by_service,
            org_id=org_id,
        )
    elif message_type == "media":
        media = payload.get("media", {})
        response = core_messaging_client.send_media_message(
            token=token,
            to_phone=phone,
            media_url=media.get("link", ""),
            media_type=media.get("type", "document"),
            caption=media.get("caption"),
            idempotency_key=dispatch.idempotency_key,
            external_reference=dispatch.external_reference,
            requested_by_service=dispatch.requested_by_service,
            org_id=org_id,
        )
    elif message_type == "template":
        template = payload.get("template", {})
        response = core_messaging_client.send_template_message(
            token=token,
            to_phone=phone,
            template_name=template.get("name", ""),
            language_code=template.get("language_code", "pt_BR"),
            components=template.get("components", []),
            idempotency_key=dispatch.idempotency_key,
            external_reference=dispatch.external_reference,
            requested_by_service=dispatch.requested_by_service,
            org_id=org_id,
        )
    else:
        response = core_messaging_client.send_text_message(
            token=token,
            to_phone=phone,
            body=payload.get("text", {}).get("body", ""),
            idempotency_key=dispatch.idempotency_key,
            external_reference=dispatch.external_reference,
            requested_by_service=dispatch.requested_by_service,
            org_id=org_id,
        )

    dispatch.core_message_public_id = str(response.get("public_id") or response.get("id") or "")
    dispatch.core_channel_account_id = str(response.get("channel_account_id") or response.get("channelAccountId") or "") or None
    dispatch.local_status = "sent"
    db.session.add(
        StudentInteraction(
            account_id=student.account_id,
            student_id=student.id,
            interaction_type="outgoing_message",
            channel="whatsapp",
            title=f"WhatsApp enviado: {dispatch.message_category}",
            body=payload.get("text", {}).get("body") or payload.get("interactive", {}).get("body"),
            created_by_user_id=student.primary_professional.user_id if student.primary_professional else None,
            interaction_at=utcnow(),
            created_at=utcnow(),
        )
    )
    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type="message_sent",
        source="whatsapp",
        title=f"WhatsApp enviado: {dispatch.message_category}",
        body=payload.get("text", {}).get("body") or payload.get("interactive", {}).get("body"),
        event_key=f"whatsapp_dispatch_sent:{dispatch.id}",
        payload={"dispatch_id": str(dispatch.id), "category": dispatch.message_category},
    )
    db.session.commit()
    return dispatch


def record_delivery_event(*, dispatch: OutboundMessageDispatch, event_type: str, payload: dict) -> OutboundMessageDispatch:
    status_map = {"sent": "sent", "delivered": "delivered", "read": "read", "failed": "failed"}
    dispatch.local_status = status_map.get(event_type, dispatch.local_status)
    db.session.add(
        WhatsAppDeliveryStatusEvent(
            outbound_dispatch_id=dispatch.id,
            core_message_public_id=dispatch.core_message_public_id,
            event_type=event_type,
            event_payload_json=payload,
            created_at=utcnow(),
        )
    )
    db.session.commit()
    return dispatch


def parse_inbound_intent(text: str | None) -> tuple[str, float]:
    normalized = (text or "").strip().lower()
    mapping = {
        "sim": ("confirm_training_yes", 0.99),
        "nao": ("confirm_training_no", 0.99),
        "mais tarde": ("confirm_training_later", 0.95),
        "treino": ("ask_for_workout", 0.92),
        "hoje": ("ask_for_today", 0.92),
        "status": ("ask_for_today", 0.8),
        "relatorio": ("ask_for_report", 0.92),
        "já treinei": ("workout_finish", 0.9),
        "ja treinei": ("workout_finish", 0.9),
        "não vou treinar": ("confirm_training_no", 0.95),
        "nao vou treinar": ("confirm_training_no", 0.95),
    }
    if normalized in mapping:
        return mapping[normalized]
    if "kg" in normalized or normalized.isdigit():
        return ("exercise_weight", 0.7)
    if normalized:
        return ("generic_text", 0.4)
    return ("generic_text", 0.1)


def record_inbound_message(*, student: StudentProfile, phone: str | None, message_type: str, text_body: str | None, media_json: dict | None, raw_payload_json: dict | None, enqueue: bool = True) -> InboundMessageRecord:
    parsed_intent, confidence = parse_inbound_intent(text_body)
    inbound = InboundMessageRecord(
        account_id=student.account_id,
        student_id=student.id,
        provider_message_id=str(raw_payload_json.get("provider_message_id") if raw_payload_json else "") or None,
        wa_from_phone=normalize_phone(phone or student.phone) or "",
        message_type=message_type,
        text_body=text_body,
        media_json=media_json or {},
        parsed_intent=parsed_intent,
        confidence=confidence,
        raw_payload_json=raw_payload_json or {},
        processed=False,
        processing_status="queued",
        received_at=utcnow(),
    )
    db.session.add(inbound)
    db.session.flush()
    create_background_job(
        job_type="process_inbound_whatsapp_message_job",
        status="queued",
        payload={"inbound_message_record_id": str(inbound.id)},
        account_id=student.account_id,
        student_id=student.id,
        reference_type="inbound_message_record",
        reference_id=inbound.id,
    )
    if enqueue:
        from app.jobs.tasks import process_inbound_whatsapp_message_job

        process_inbound_whatsapp_message_job.delay(str(inbound.id))
    db.session.commit()
    return inbound


def process_inbound_message(inbound_id: str) -> dict:
    inbound = InboundMessageRecord.query.filter_by(id=inbound_id).first()
    if inbound is None:
        raise ApiError("Inbound não encontrado.", HTTPStatus.NOT_FOUND)
    student = require_student(inbound.account_id, inbound.student_id)
    now = utcnow()
    interaction = StudentInteraction(
        account_id=student.account_id,
        student_id=student.id,
        interaction_type="incoming_message",
        channel="whatsapp",
        title=f"Inbound WhatsApp: {inbound.parsed_intent or 'generic'}",
        body=inbound.text_body,
        created_by_user_id=None,
        interaction_at=now,
        created_at=now,
    )
    db.session.add(interaction)

    signal_type = "message"
    signal_title = inbound.text_body or "Mensagem recebida no WhatsApp"
    response_text = "Entendi. Vou registrar isso e seu profissional podera ver no acompanhamento."

    if inbound.parsed_intent == "confirm_training_yes":
        signal_type = "manual_note"
        signal_title = "Aluno confirmou que vai treinar hoje"
        response_text = "Perfeito ✅ Vou te mandar o treino de hoje."
    elif inbound.parsed_intent == "confirm_training_no":
        signal_type = "absence"
        signal_title = "Aluno informou que não vai treinar hoje"
        response_text = "Tudo bem. Vou registrar isso e ajustar o acompanhamento."
    elif inbound.parsed_intent == "ask_for_workout":
        signal_title = "Aluno pediu o treino no WhatsApp"
        response_text = "Certo. Vou te mandar o treino ativo."
    elif inbound.parsed_intent == "workout_finish":
        signal_type = "workout"
        signal_title = "Aluno informou treino concluído pelo WhatsApp"
        response_text = "Boa! Vou registrar seu treino e seguir acompanhando."
    elif inbound.message_type in {"image", "media"}:
        signal_type = "meal"
        signal_title = "Aluno enviou refeição/foto no WhatsApp"
        response_text = "Recebi sua refeição 🍽️ Vou registrar isso no acompanhamento."

    db.session.add(
        StudentDailySignal(
            account_id=student.account_id,
            student_id=student.id,
            signal_date=now.date(),
            signal_type=signal_type,
            source="whatsapp",
            title=signal_title,
            body=inbound.text_body,
            payload_json={"inbound_message_record_id": str(inbound.id), "intent": inbound.parsed_intent},
            created_by_user_id=None,
            created_at=now,
        )
    )
    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type=_event_type_for_signal(signal_type),
        source="whatsapp",
        title=signal_title,
        body=inbound.text_body,
        event_key=f"inbound_signal:{inbound.id}",
        severity="warning" if signal_type == "absence" else "info",
        payload={"inbound_message_record_id": str(inbound.id), "intent": inbound.parsed_intent, "signal_type": signal_type},
    )

    _upsert_session(student=student, flow="generic", step="handled", context={"last_intent": inbound.parsed_intent})
    inbound.processed = True
    inbound.processing_status = "completed"

    if inbound.parsed_intent == "confirm_training_yes":
        send_workout_of_day(student=student, actor_user_id=student.primary_professional.user_id if student.primary_professional else None, enqueue=False)
    elif inbound.parsed_intent == "ask_for_workout":
        send_workout_of_day(student=student, actor_user_id=student.primary_professional.user_id if student.primary_professional else None, enqueue=False)
    else:
        send_manual_whatsapp_message(
            student=student,
            actor_user_id=student.primary_professional.user_id if student.primary_professional else None,
            message_text=response_text,
            enqueue=False,
        )
    recompute_and_persist_score(student)
    evaluate_retention_automation(student)
    db.session.commit()
    return {"status": "completed", "intent": inbound.parsed_intent}


def _event_type_for_signal(signal_type: str) -> str:
    return {
        "workout": "workout_completed",
        "meal": "meal_logged",
        "absence": "absence_detected",
        "message": "response_received",
        "manual_note": "response_received",
    }.get(signal_type, "signal_recorded")
