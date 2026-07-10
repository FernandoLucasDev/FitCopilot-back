from __future__ import annotations

from datetime import datetime, timezone

from app.common.db.mixins import utcnow
from app.events.models import EventSource, EventType
from app.extensions import db
from app.integrations.academy.base import NormalizedAcademyEvent
from app.integrations.academy.models import AcademyWebhookLog, ExternalSystemMapping
from app.operations.services import emit_event

# Nenhum conector concreto está registrado ainda (ver app/integrations/academy/base.py).
# Quando um conector real for adicionado, ele entra aqui: {"tecnofit": TecnofitConnector(), ...}
CONNECTORS: dict[str, object] = {}


def _generic_parse(payload: dict) -> NormalizedAcademyEvent:
    """Fallback usado quando não há um conector específico registrado para o provider.

    Espera o payload já no formato normalizado (external_student_id, event_type,
    occurred_at, external_event_id) — é o que permite testar a arquitetura ponta a
    ponta antes de existir um conector real de fornecedor.
    """
    occurred_at_raw = payload.get("occurred_at")
    occurred_at = datetime.fromisoformat(occurred_at_raw) if occurred_at_raw else utcnow()
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return NormalizedAcademyEvent(
        external_student_id=str(payload.get("external_student_id")),
        event_type=payload.get("event_type", EventType.ACADEMY_CHECKIN_DETECTED),
        occurred_at=occurred_at,
        external_event_id=str(payload.get("external_event_id")),
        raw=payload,
    )


def parse_academy_webhook(*, provider: str, payload: dict) -> NormalizedAcademyEvent:
    connector = CONNECTORS.get(provider)
    if connector is not None:
        return connector.parse_webhook_event(payload)
    return _generic_parse(payload)


def process_academy_webhook(*, account_id: str, provider: str, payload: dict) -> dict:
    """Processa um evento de webhook de academia com idempotência por (provider, external_event_id).

    Retorna {"status": "processed" | "duplicate" | "unmapped", "eventId": str | None}.
    Falhas de mapeamento (aluno externo sem StudentProfile vinculado) ficam registradas
    com status "unmapped" em vez de erro 500 — o conector pode não ter contexto ainda
    (aluno recém-cadastrado no sistema de academia mas não no FitCopilot).
    """
    normalized = parse_academy_webhook(provider=provider, payload=payload)

    existing = AcademyWebhookLog.query.filter_by(provider=provider, external_event_id=normalized.external_event_id).first()
    if existing is not None:
        return {"status": "duplicate", "eventId": None}

    mapping = ExternalSystemMapping.query.filter_by(
        account_id=account_id, provider=provider, external_student_id=normalized.external_student_id
    ).first()

    if mapping is None:
        db.session.add(
            AcademyWebhookLog(
                account_id=account_id,
                provider=provider,
                external_event_id=normalized.external_event_id,
                status="unmapped",
                payload_json=normalized.raw,
                received_at=utcnow(),
            )
        )
        db.session.commit()
        return {"status": "unmapped", "eventId": None}

    event_title = "Check-in detectado na academia" if normalized.event_type == EventType.ACADEMY_CHECKIN_DETECTED else "Ausência detectada na academia"
    event = emit_event(
        account_id=account_id,
        student_id=mapping.student_id,
        event_type=normalized.event_type,
        source=EventSource.ACADEMY,
        title=event_title,
        occurred_at=normalized.occurred_at,
        payload={"provider": provider, "externalStudentId": normalized.external_student_id},
    )
    db.session.add(
        AcademyWebhookLog(
            account_id=account_id,
            provider=provider,
            external_event_id=normalized.external_event_id,
            status="processed",
            payload_json=normalized.raw,
            received_at=utcnow(),
        )
    )
    db.session.commit()
    return {"status": "processed", "eventId": str(event.id)}
