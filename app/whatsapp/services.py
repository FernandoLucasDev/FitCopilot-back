from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from uuid import uuid4

import requests
from flask import current_app

from app.common.api import ApiError
from app.extensions import db
from app.integrations.core_messaging_client import core_messaging_client
from app.jobs.services import create_audit_log, create_background_job
from app.messaging.models import SuggestedMessage
from app.nutrition.plan_services import get_active_nutrition_plan_for_student, serialize_nutrition_plan
from app.operations.services import emit_event, evaluate_retention_automation, recompute_and_persist_score
from app.students.models import StudentDailySignal, StudentInteraction, StudentProfile
from app.students.portal_models import StudentLoginChallenge
from app.students.services import require_student
from app.whatsapp.models import (
    InboundMessageRecord,
    OutboundMessageDispatch,
    WhatsAppAutomationRule,
    WhatsAppDeliveryStatusEvent,
    WhatsAppSession,
)
from app.workouts.models import WorkoutSession
from app.workouts.services import complete_workout_session_without_logs, get_active_workout_for_student, serialize_workout_plan


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


def phone_variants(phone: str | None) -> list[str]:
    normalized = normalize_phone(phone)
    if not normalized:
        return []
    variants = [normalized]
    if normalized.startswith("55") and len(normalized) == 13 and normalized[4:5] == "9":
        variants.append(f"{normalized[:4]}{normalized[5:]}")
    elif normalized.startswith("55") and len(normalized) == 12:
        variants.append(f"{normalized[:4]}9{normalized[4:]}")
    return list(dict.fromkeys(variants))


def _owner_context(student: StudentProfile) -> tuple[str, str | None]:
    account_users = list(student.account.users) if student.account and student.account.users else []
    role_priority = {"owner": 0, "admin": 1, "professional": 2}
    eligible_users = [user for user in account_users if user.core_access_token]
    owner = next(
        iter(
            sorted(
                eligible_users,
                key=lambda user: role_priority.get(str(user.role or "").lower(), 99),
            )
        ),
        None,
    )
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
    existing_rules = WhatsAppAutomationRule.query.filter_by(account_id=student.account_id).all()
    existing_rule_types = {rule.rule_type for rule in existing_rules}
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
        WhatsAppAutomationRule(
            account_id=student.account_id,
            name="Fechamento do dia",
            rule_type="daily_report",
            is_active=True,
            schedule_json={"hour": current_app.config.get("WHATSAPP_DAILY_REPORT_HOUR", 20)},
            filters_json={"student_id": str(student.id)},
            template_config_json={},
        ),
    ]
    if getattr(student.account, "professional_vertical", None) == "nutricionista":
        defaults.extend(
            [
                WhatsAppAutomationRule(
                    account_id=student.account_id,
                    name="Sem refeição há 2 dias",
                    rule_type="nutrition_no_log_2d",
                    is_active=True,
                    schedule_json={"threshold_days": 2},
                    filters_json={},
                    template_config_json={},
                ),
                WhatsAppAutomationRule(
                    account_id=student.account_id,
                    name="Meta calórica ultrapassada 3 dias seguidos",
                    rule_type="nutrition_over_target_3d",
                    is_active=True,
                    schedule_json={"threshold_days": 3},
                    filters_json={},
                    template_config_json={},
                ),
            ]
        )

    missing_defaults = [rule for rule in defaults if rule.rule_type not in existing_rule_types]
    if not missing_defaults:
        return existing_rules

    db.session.add_all(missing_defaults)
    db.session.commit()
    return existing_rules + missing_defaults


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
    if data.nutrition_no_log_active is not None and "nutrition_no_log_2d" in rules:
        rules["nutrition_no_log_2d"].is_active = data.nutrition_no_log_active
    if data.nutrition_over_target_active is not None and "nutrition_over_target_3d" in rules:
        rules["nutrition_over_target_3d"].is_active = data.nutrition_over_target_active
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
    professional_name = (
        student.primary_professional.user.full_name
        if student.primary_professional and student.primary_professional.user
        else "seu personal"
    )
    portal_url = str(current_app.config.get("STUDENT_PORTAL_URL") or "https://fitcopilot.com.br/aluno")
    login_hint = (
        f"\n\nSua área do aluno: {portal_url}\n"
        f"Para entrar, use este e-mail: {student.email}. O código de acesso chega por aqui, no WhatsApp."
        if student.email
        else f"\n\nSua área do aluno: {portal_url}\nSe precisar acessar, peça para {professional_name} cadastrar seu e-mail."
    )
    body = (
        f"Oi, {student.full_name.split()[0]}! 👋 Seu acompanhamento começou. Eu sou o Agente Fit, assistente do {professional_name}.\n\n"
        "Por aqui você pode avisar quando treinou, mandar foto ou descrição das refeições, tirar dúvidas rápidas e receber lembretes do seu acompanhamento.\n\n"
        "Pra usar é simples: me responda como falaria no WhatsApp mesmo. Ex: “treinei hoje”, “almoço: arroz, feijão e frango” ou envie uma foto do prato. 💪"
        f"{login_hint}"
    )
    session = _upsert_session(student=student, flow="onboarding", step="awaiting_confirmation", context={"source": "student_created"})
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
    portal_url = str(current_app.config.get("STUDENT_PORTAL_URL") or "http://127.0.0.1:3000/aluno")
    first_name = student.full_name.split()[0] if student.full_name else "aluno"
    body = (
        f"Oi, {first_name}! Seu treino de hoje já está na sua ficha: {plan.title} 💪\n\n"
        f"São {exercises_count} exercícios. Abra o link para ver tudo detalhado e registrar a carga de cada exercício:\n"
        f"{portal_url}\n\n"
        "Se pedir código, use seu e-mail cadastrado. Eu envio o acesso por aqui, no WhatsApp."
    )
    session = _upsert_session(student=student, flow="workout_execution", step="portal_link_sent", context={"plan_id": str(plan.id), "portal_url": portal_url})
    return queue_whatsapp_dispatch(
        student=student,
        actor_user_id=actor_user_id,
        message_category="workout_delivery",
        related_entity_type="workout_plan",
        related_entity_id=plan.id,
        idempotency_key=_idempotency_key("workout-of-day", student, date.today().isoformat()),
        external_reference=f"student:{student.id}:workout_of_day:{date.today().isoformat()}",
        payload=_build_text_payload(body=body),
        enqueue=enqueue,
    )


def send_nutrition_plan_of_day(*, student: StudentProfile, actor_user_id, enqueue: bool = True) -> OutboundMessageDispatch:
    plan = get_active_nutrition_plan_for_student(student.id)
    if not plan:
        raise ApiError("Paciente sem plano alimentar ativo para envio.", HTTPStatus.CONFLICT)
    serialized = serialize_nutrition_plan(plan)
    meals_count = len(serialized["meals"])
    portal_url = str(current_app.config.get("STUDENT_PORTAL_URL") or "http://127.0.0.1:3000/aluno")
    first_name = student.full_name.split()[0] if student.full_name else "paciente"
    body = (
        f"Oi, {first_name}! Seu plano alimentar de hoje já está pronto: {plan.title} 🥗\n\n"
        f"São {meals_count} refeições planejadas. Abra o link para ver tudo detalhado:\n"
        f"{portal_url}\n\n"
        "Se pedir código, use seu e-mail cadastrado. Eu envio o acesso por aqui, no WhatsApp."
    )
    session = _upsert_session(
        student=student,
        flow="nutrition_plan_view",
        step="portal_link_sent",
        context={"plan_id": str(plan.id), "portal_url": portal_url},
    )
    return queue_whatsapp_dispatch(
        student=student,
        actor_user_id=actor_user_id,
        message_category="nutrition_plan_delivery",
        related_entity_type="nutrition_plan",
        related_entity_id=plan.id,
        idempotency_key=_idempotency_key("nutrition-plan-of-day", student, date.today().isoformat()),
        external_reference=f"student:{student.id}:nutrition_plan_of_day:{date.today().isoformat()}",
        payload=_build_text_payload(body=body),
        enqueue=enqueue,
    )


def _positive_workout_completion_reply(text: str | None, parsed_intent: str | None = None) -> bool:
    normalized = (text or "").strip().lower()
    positive_tokens = {
        "s",
        "sim",
        "ss",
        "ok",
        "pronto",
        "feito",
        "acabei",
        "terminei",
        "terminei sim",
        "finalizei",
        "finalizei sim",
        "conclui",
        "conclui sim",
        "ja",
        "já",
        "ja treinei",
        "já treinei",
        "ja terminei",
        "já terminei",
    }
    if parsed_intent in {"workout_finish", "confirm_training_yes"}:
        return True
    if normalized in positive_tokens:
        return True
    return any(phrase in normalized for phrase in ["terminei", "finalizei", "conclui", "treino feito", "ja treinei", "já treinei"])


def _latest_pending_workout_session(student: StudentProfile) -> WorkoutSession | None:
    return (
        WorkoutSession.query.filter_by(student_id=student.id, status="pending")
        .order_by(WorkoutSession.created_at.desc())
        .first()
    )


def _send_workout_completion_check(session: WorkoutSession, *, repeat_index: int, enqueue: bool = True) -> OutboundMessageDispatch:
    student = session.student
    first_name = student.full_name.split()[0] if student.full_name else "aluno"
    body = (
        f"Oi, {first_name}! Vi que seu treino ainda ficou aberto por aqui. 💪\n\n"
        "Você já terminou?\n"
        "Pode responder só *sim* ou *não*."
    )
    return queue_whatsapp_dispatch(
        student=student,
        actor_user_id=student.primary_professional.user_id if student.primary_professional else None,
        message_category="workout_completion_check",
        related_entity_type="workout_session",
        related_entity_id=session.id,
        idempotency_key=_idempotency_key("workout-completion-check", student, f"{session.id}:{repeat_index}"),
        external_reference=f"student:{student.id}:workout_completion_check:{session.id}:{repeat_index}",
        payload=_build_text_payload(body=body),
        enqueue=enqueue,
    )


def _send_workout_auto_completed_notice(session: WorkoutSession, *, enqueue: bool = True) -> OutboundMessageDispatch:
    student = session.student
    first_name = student.full_name.split()[0] if student.full_name else "aluno"
    body = (
        f"{first_name}, como seu treino ficou aberto por algumas horas, finalizei automaticamente por aqui. ✅\n\n"
        "Se algo ficou diferente, me responde aqui com uma observação que eu deixo registrado para seu personal."
    )
    return queue_whatsapp_dispatch(
        student=student,
        actor_user_id=student.primary_professional.user_id if student.primary_professional else None,
        message_category="workout_auto_completed_notice",
        related_entity_type="workout_session",
        related_entity_id=session.id,
        idempotency_key=_idempotency_key("workout-auto-completed", student, str(session.id)),
        external_reference=f"student:{student.id}:workout_auto_completed:{session.id}",
        payload=_build_text_payload(body=body),
        enqueue=enqueue,
    )


def check_pending_workout_sessions(*, now: datetime | None = None) -> dict:
    now = now or utcnow()
    sessions = WorkoutSession.query.filter_by(status="pending").order_by(WorkoutSession.created_at.asc()).all()
    checked = prompted = auto_completed = skipped = 0
    for session in sessions:
        checked += 1
        student = session.student
        if not student or student.archived_at is not None:
            skipped += 1
            continue

        created_at = session.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age = now - created_at
        checks = (
            OutboundMessageDispatch.query.filter_by(
                related_entity_type="workout_session",
                related_entity_id=str(session.id),
                message_category="workout_completion_check",
            )
            .order_by(OutboundMessageDispatch.created_at.desc())
            .all()
        )
        latest_check = checks[0] if checks else None

        if age >= timedelta(hours=5):
            complete_workout_session_without_logs(session=session, actor_user_id=None, note="Finalizado automaticamente apos 5h sem conclusao manual.")
            _send_workout_auto_completed_notice(session)
            auto_completed += 1
            continue

        if age < timedelta(hours=2):
            skipped += 1
            continue

        should_prompt = latest_check is None
        if latest_check is not None:
            last_check_at = latest_check.created_at
            if last_check_at.tzinfo is None:
                last_check_at = last_check_at.replace(tzinfo=timezone.utc)
            has_reply_after_check = InboundMessageRecord.query.filter(
                InboundMessageRecord.student_id == session.student_id,
                InboundMessageRecord.received_at > last_check_at,
            ).first()
            should_prompt = has_reply_after_check is None and (now - last_check_at) >= timedelta(hours=2)

        if should_prompt:
            _send_workout_completion_check(session, repeat_index=len(checks) + 1)
            prompted += 1
        else:
            skipped += 1

    return {"status": "completed", "checked": checked, "prompted": prompted, "auto_completed": auto_completed, "skipped": skipped}


def _signals_for_day(student: StudentProfile, target_date: date, signal_type: str | None = None) -> list[StudentDailySignal]:
    query = StudentDailySignal.query.filter_by(student_id=student.id, signal_date=target_date)
    if signal_type:
        query = query.filter_by(signal_type=signal_type)
    return query.order_by(StudentDailySignal.created_at.asc()).all()


def _meal_calorie_range(meal: StudentDailySignal) -> tuple[int, int] | None:
    payload = meal.payload_json or {}
    calorie_range = payload.get("calorie_range") or {}
    low = calorie_range.get("min")
    high = calorie_range.get("max")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        return int(low), int(high)
    estimated = payload.get("estimated_calories")
    if isinstance(estimated, (int, float)):
        value = int(estimated)
        return value, value
    return None


def _daily_calorie_range(student: StudentProfile, target_date: date) -> tuple[int, int] | None:
    low_total = 0
    high_total = 0
    found = False
    for meal in _signals_for_day(student, target_date, "meal"):
        item_range = _meal_calorie_range(meal)
        if item_range is None:
            continue
        low, high = item_range
        low_total += low
        high_total += high
        found = True
    if not found:
        return None
    return low_total, high_total


def _format_daily_calories(calorie_range: tuple[int, int] | None) -> str:
    if calorie_range is None:
        return "Ainda não tenho calorias suficientes para estimar com segurança."
    low, high = calorie_range
    if low == high:
        return f"Total estimado: cerca de {low} kcal."
    return f"Total estimado: entre {low} e {high} kcal."


def _tomorrow_recommendation(*, meals_count: int, calorie_range: tuple[int, int] | None, workout_count: int) -> str:
    if meals_count == 0:
        return "Amanhã, me manda pelo menos uma refeição e o treino quando concluir. Assim seu personal acompanha melhor."
    if calorie_range is not None:
        low, high = calorie_range
        if high < 1400:
            return "Amanhã, tenta não deixar grandes janelas sem comer e garante uma fonte de proteína nas principais refeições."
        if low > 2800:
            return "Amanhã, mantém proteína e hidratação, mas observa porções de carboidrato e gordura para não passar muito do alvo."
    if workout_count == 0:
        return "Amanhã, se for treinar, evita ir em jejum longo e me avisa quando concluir para eu fechar melhor seu dia."
    return "Amanhã, mantém boa hidratação e tenta repetir o básico: proteína, carboidrato na medida e consistência."


def build_end_of_day_report_text(student: StudentProfile, target_date: date | None = None) -> str:
    target_date = target_date or date.today()
    meals = _signals_for_day(student, target_date, "meal")
    workouts = _signals_for_day(student, target_date, "workout")
    calorie_range = _daily_calorie_range(student, target_date)
    first_name = student.full_name.split()[0]
    meal_line = f"Hoje registrei {len(meals)} refeição." if len(meals) == 1 else f"Hoje registrei {len(meals)} refeições."
    workout_line = "Treino: registrado hoje." if workouts else "Treino: ainda não registrado hoje."
    recommendation = _tomorrow_recommendation(
        meals_count=len(meals),
        calorie_range=calorie_range,
        workout_count=len(workouts),
    )
    return (
        f"Fechamento do dia, {first_name} 🌙\n\n"
        f"{meal_line}\n"
        f"{_format_daily_calories(calorie_range)}\n"
        f"{workout_line}\n\n"
        f"Para amanhã: {recommendation}"
    )


def send_end_of_day_report(
    *,
    student: StudentProfile,
    actor_user_id,
    summary_date: date | None = None,
    enqueue: bool = True,
) -> OutboundMessageDispatch:
    phone = normalize_phone(student.phone)
    if not phone:
        raise ApiError("Aluno sem telefone válido para WhatsApp.", HTTPStatus.CONFLICT)
    target_date = summary_date or date.today()
    body = build_end_of_day_report_text(student, target_date)
    session = _upsert_session(
        student=student,
        flow="daily_report",
        step="sent",
        context={"summary_date": target_date.isoformat()},
    )
    return queue_whatsapp_dispatch(
        student=student,
        actor_user_id=actor_user_id,
        message_category="daily_report",
        related_entity_type="whatsapp_session",
        related_entity_id=session.id,
        idempotency_key=_idempotency_key("daily-report", student, target_date.isoformat()),
        external_reference=f"student:{student.id}:daily_report:{target_date.isoformat()}",
        payload=_build_text_payload(body=body),
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


def send_student_otp_message(*, student: StudentProfile, code: str, challenge_id, enqueue: bool = True) -> OutboundMessageDispatch:
    phone = normalize_phone(student.phone)
    if not phone:
        raise ApiError("Aluno sem telefone valido para WhatsApp.", HTTPStatus.CONFLICT)
    first_name = (student.full_name or "aluno").split()[0]
    body = (
        f"Oi, {first_name}! Seu codigo de acesso ao FitCopilot e: {code}\n\n"
        "Ele expira em 10 minutos. Se voce nao pediu esse codigo, pode ignorar esta mensagem."
    )
    session = _upsert_session(
        student=student,
        flow="student_login_otp",
        step="otp_sent",
        context={"challenge_id": str(challenge_id)},
    )
    return queue_whatsapp_dispatch(
        student=student,
        actor_user_id=student.primary_professional.user_id if student.primary_professional else None,
        message_category="student_otp",
        related_entity_type="student_login_challenge",
        related_entity_id=challenge_id,
        idempotency_key=_idempotency_key("student-otp", student, str(challenge_id)),
        external_reference=f"student:{student.id}:otp:{challenge_id}",
        payload=_build_text_payload(body=body),
        enqueue=enqueue,
    )


def send_professional_note_whatsapp_message(*, student: StudentProfile, actor_user_id, message_text: str, enqueue: bool = True) -> OutboundMessageDispatch:
    clean_text = (message_text or "").strip()
    if not clean_text:
        raise ApiError("Mensagem vazia.", HTTPStatus.BAD_REQUEST)
    professional_user = student.primary_professional.user if student.primary_professional and student.primary_professional.user else None
    professional_phone = normalize_phone(professional_user.phone) if professional_user and professional_user.phone else None
    contact_line = (
        f"Se quiser alinhar algum detalhe, fale direto com ele pelo {professional_phone} ou pessoalmente."
        if professional_phone
        else "Se quiser alinhar algum detalhe, fale direto com ele pelo WhatsApp pessoal ou pessoalmente."
    )
    body = (
        f"Olá, {student.full_name.split()[0]}! Seu personal deixou um recado:\n\n"
        f"{clean_text}\n\n"
        f"{contact_line}"
    )
    return send_manual_whatsapp_message(
        student=student,
        actor_user_id=actor_user_id,
        message_text=body,
        message_type="text",
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


def _dispatch_text_body(payload: dict) -> str:
    message_type = payload.get("message_type", "text")
    if message_type == "interactive":
        body = payload.get("interactive", {}).get("body", "")
        buttons = payload.get("interactive", {}).get("buttons", [])
        options = [str(item.get("title") or "").strip() for item in buttons if item.get("title")]
        if options:
            return f"{body}\n\nOpções: {', '.join(options)}"
        return body
    if message_type == "media":
        media = payload.get("media", {})
        return str(media.get("caption") or media.get("link") or "")
    if message_type == "template":
        template = payload.get("template", {})
        return str(template.get("body") or template.get("name") or "")
    return str(payload.get("text", {}).get("body", ""))


def _send_dispatch_via_local_bot(*, dispatch: OutboundMessageDispatch, student: StudentProfile, phone: str, payload: dict) -> dict:
    base_url = str(current_app.config.get("WWP_BOT_INTERNAL_URL") or "").rstrip("/")
    if not base_url:
        raise RuntimeError("WWP_BOT_INTERNAL_URL não configurado")
    body = _dispatch_text_body(payload)
    if not body:
        raise RuntimeError("Mensagem sem texto para envio pelo bot local")
    response = requests.post(
        f"{base_url}/internal/messages/send",
        json={"phoneNumber": phone, "text": body},
        timeout=float(current_app.config.get("CORE_TIMEOUT_SECONDS", 15)),
    )
    response.raise_for_status()
    data = response.json()
    return {
        "public_id": data.get("result", {}).get("key", {}).get("id") or f"wwp-bot:{dispatch.id}",
        "channel_account_id": "wwp-bot-local",
    }


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

    payload = dispatch.payload_json or {}
    message_type = payload.get("message_type", "text")
    provider_mode = str(current_app.config.get("WHATSAPP_PROVIDER_MODE", "core") or "core").strip().lower()
    if provider_mode not in {"core", "evolution", "core_with_evolution_fallback"}:
        raise RuntimeError(f"WHATSAPP_PROVIDER_MODE invalido: {provider_mode}")
    try:
        if provider_mode == "evolution":
            response = _send_dispatch_via_local_bot(dispatch=dispatch, student=student, phone=phone, payload=payload)
        else:
            response = _send_dispatch_via_core(
                dispatch=dispatch,
                student=student,
                phone=phone,
                payload=payload,
                message_type=message_type,
            )
    except Exception as exc:
        if provider_mode != "core_with_evolution_fallback":
            raise
        current_app.logger.warning(
            "core_whatsapp_dispatch_failed_using_explicit_evolution_fallback dispatch_id=%s error=%s",
            dispatch.id,
            exc,
        )
        response = _send_dispatch_via_local_bot(dispatch=dispatch, student=student, phone=phone, payload=payload)

    dispatch.core_message_public_id = str(response.get("public_id") or response.get("id") or "")
    dispatch.core_channel_account_id = str(response.get("channel_account_id") or response.get("channelAccountId") or "") or None
    dispatch.local_status = str(response.get("status") or "accepted").lower()
    db.session.add(
        StudentInteraction(
            account_id=student.account_id,
            student_id=student.id,
            interaction_type="outgoing_message",
            channel="whatsapp",
            title=f"WhatsApp aceito: {dispatch.message_category}",
            body=payload.get("text", {}).get("body") or payload.get("interactive", {}).get("body"),
            created_by_user_id=student.primary_professional.user_id if student.primary_professional else None,
            interaction_at=utcnow(),
            created_at=utcnow(),
        )
    )
    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type="message_accepted",
        source="whatsapp",
        title=f"WhatsApp aceito para envio: {dispatch.message_category}",
        body=payload.get("text", {}).get("body") or payload.get("interactive", {}).get("body"),
        event_key=f"whatsapp_dispatch_accepted:{dispatch.id}",
        payload={"dispatch_id": str(dispatch.id), "category": dispatch.message_category},
    )
    db.session.commit()
    return dispatch


def _send_dispatch_via_core(
    *,
    dispatch: OutboundMessageDispatch,
    student: StudentProfile,
    phone: str,
    payload: dict,
    message_type: str,
) -> dict:
    token, org_id = _owner_context(student)
    if message_type == "interactive":
        return core_messaging_client.send_interactive_message(
                token=token,
                to_phone=phone,
                body=payload.get("interactive", {}).get("body", ""),
                buttons=payload.get("interactive", {}).get("buttons", []),
                idempotency_key=dispatch.idempotency_key,
                external_reference=dispatch.external_reference,
                requested_by_service=dispatch.requested_by_service,
                org_id=org_id,
        )
    if message_type == "media":
        media = payload.get("media", {})
        return core_messaging_client.send_media_message(
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
    if message_type == "template":
        template = payload.get("template", {})
        return core_messaging_client.send_template_message(
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
    return core_messaging_client.send_text_message(
        token=token,
        to_phone=phone,
        body=payload.get("text", {}).get("body", ""),
        idempotency_key=dispatch.idempotency_key,
        external_reference=dispatch.external_reference,
        requested_by_service=dispatch.requested_by_service,
        org_id=org_id,
    )


def record_delivery_event(*, dispatch: OutboundMessageDispatch, event_type: str, payload: dict) -> OutboundMessageDispatch:
    event_type = (event_type or "").strip().lower()
    status_map = {
        "queued": "queued",
        "processing": "processing",
        "accepted": "accepted",
        "sent": "sent",
        "delivered": "delivered",
        "read": "read",
        "failed": "failed",
    }
    dispatch.local_status = status_map.get(event_type, dispatch.local_status)
    provider_event_id = str(payload.get("providerEventId") or "").strip()
    existing = WhatsAppDeliveryStatusEvent.query.filter_by(
        outbound_dispatch_id=dispatch.id,
        event_type=event_type,
    ).order_by(WhatsAppDeliveryStatusEvent.created_at.desc()).first()
    existing_provider_event_id = str((existing.event_payload_json or {}).get("providerEventId") or "") if existing else ""
    if existing is None or (provider_event_id and provider_event_id != existing_provider_event_id):
        db.session.add(
            WhatsAppDeliveryStatusEvent(
                outbound_dispatch_id=dispatch.id,
                core_message_public_id=dispatch.core_message_public_id,
                event_type=event_type,
                event_payload_json=payload,
                created_at=utcnow(),
            )
        )
    if event_type in {"delivered", "read", "failed"}:
        if dispatch.message_category == "student_otp" and dispatch.related_entity_type == "student_login_challenge":
            challenge = db.session.get(StudentLoginChallenge, dispatch.related_entity_id)
            if challenge is not None:
                challenge.delivery_status = "failed" if event_type == "failed" else "sent"
        emit_event(
            account_id=dispatch.account_id,
            student_id=dispatch.student_id,
            event_type=f"message_{event_type}",
            source="whatsapp_core",
            title="Falha no envio pelo WhatsApp" if event_type == "failed" else f"WhatsApp {event_type}",
            body=payload.get("providerErrorMessage") or dispatch.message_category,
            severity="critical" if event_type == "failed" else "info",
            event_key=f"whatsapp_delivery:{dispatch.id}:{event_type}:{provider_event_id or 'current'}",
            payload=payload,
        )
    db.session.commit()
    return dispatch


def apply_core_delivery_status(payload: dict) -> tuple[OutboundMessageDispatch, bool]:
    public_id = str(payload.get("coreMessagePublicId") or "").strip()
    external_reference = str(payload.get("externalReference") or "").strip()
    dispatch = None
    if public_id:
        dispatch = OutboundMessageDispatch.query.filter_by(core_message_public_id=public_id).first()
    if dispatch is None and external_reference:
        dispatch = OutboundMessageDispatch.query.filter_by(external_reference=external_reference).first()
    if dispatch is None:
        raise ApiError("Dispatch do Core ainda nao esta disponivel.", HTTPStatus.NOT_FOUND)
    previous_status = dispatch.local_status
    record_delivery_event(dispatch=dispatch, event_type=str(payload.get("status") or ""), payload=payload)
    return dispatch, dispatch.local_status != previous_status


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


def observe_core_inbound_message(
    *,
    phone_number: str | None,
    text: str | None,
    message_type: str,
    metadata: dict,
) -> tuple[InboundMessageRecord | None, bool]:
    provider_message_id = str(metadata.get("providerMessageId") or "").strip()
    if provider_message_id:
        existing = InboundMessageRecord.query.filter_by(provider_message_id=provider_message_id).first()
        if existing is not None:
            return existing, True

    inbound_phone_variants = phone_variants(phone_number)
    if not inbound_phone_variants:
        return None, False
    students = StudentProfile.query.filter(
        StudentProfile.phone.isnot(None),
        StudentProfile.archived_at.is_(None),
    ).order_by(StudentProfile.created_at.desc()).all()
    student = next(
        (
            item
            for item in students
            if any(variant in inbound_phone_variants for variant in phone_variants(item.phone))
        ),
        None,
    )
    if student is None:
        return None, False

    now = utcnow()
    parsed_intent, confidence = parse_inbound_intent(text)
    inbound = InboundMessageRecord(
        account_id=student.account_id,
        student_id=student.id,
        provider_message_id=provider_message_id or None,
        wa_from_phone=inbound_phone_variants[0],
        message_type=message_type or "text",
        text_body=text,
        media_json=metadata.get("media") or {},
        parsed_intent=parsed_intent,
        confidence=confidence,
        raw_payload_json=metadata.get("rawPayload") or {},
        processed=True,
        processing_status="completed",
        received_at=now,
    )
    db.session.add(inbound)
    student.last_contact_at = now
    student.last_activity_at = now
    student.last_signal_summary = "Resposta recebida pelo WhatsApp"
    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type="response_received",
        source="whatsapp_core",
        title="Mensagem recebida pelo WhatsApp",
        body=text,
        severity="info",
        event_key=f"whatsapp_inbound:{provider_message_id or inbound.id}",
        payload={
            "provider_message_id": provider_message_id,
            "core_message_public_id": metadata.get("coreMessagePublicId"),
            "message_type": message_type,
        },
    )
    recompute_and_persist_score(student)
    evaluate_retention_automation(student)
    db.session.commit()
    return inbound, False


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
    pending_workout_session = _latest_pending_workout_session(student)
    answered_pending_workout = bool(pending_workout_session and _positive_workout_completion_reply(inbound.text_body, inbound.parsed_intent))

    if answered_pending_workout:
        signal_type = "workout"
        signal_title = "Aluno confirmou treino concluido pelo WhatsApp"
        response_text = "Boa! Finalizei seu treino por aqui ?"
    elif inbound.parsed_intent == "confirm_training_yes":
        signal_type = "manual_note"
        signal_title = "Aluno confirmou que vai treinar hoje"
        response_text = "Perfeito ? Vou te mandar o treino de hoje."
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

    if answered_pending_workout and pending_workout_session:
        complete_workout_session_without_logs(
            session=pending_workout_session,
            actor_user_id=None,
            note=f"Finalizado por resposta no WhatsApp: {inbound.text_body or inbound.parsed_intent}",
        )
        send_manual_whatsapp_message(
            student=student,
            actor_user_id=student.primary_professional.user_id if student.primary_professional else None,
            message_text=response_text,
            enqueue=False,
        )
    elif inbound.parsed_intent == "confirm_training_yes":
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
