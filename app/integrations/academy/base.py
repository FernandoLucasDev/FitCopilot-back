from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class NormalizedAcademyEvent:
    """Formato comum para eventos vindos de qualquer sistema de academia."""

    external_student_id: str
    event_type: str  # ver app.events.models.EventType: ACADEMY_CHECKIN_DETECTED | ACADEMY_ABSENCE_DETECTED
    occurred_at: datetime
    external_event_id: str
    raw: dict


class AcademyConnector(Protocol):
    """
    Interface que um conector de sistema de academia (Tecnofit, Pacto, Evo, ...)
    deve implementar para se plugar no webhook genérico de `app.integrations.academy.routes`.

    Nenhuma implementação concreta existe ainda — esta é só a arquitetura, pronta
    para receber um conector real quando houver acesso a um fornecedor. Um conector
    futuro ficaria em `app/integrations/academy/providers/<nome>.py`, por exemplo:

        class TecnofitConnector:
            provider = "tecnofit"

            def parse_webhook_event(self, payload: dict) -> NormalizedAcademyEvent:
                return NormalizedAcademyEvent(
                    external_student_id=str(payload["aluno_id"]),
                    event_type=EventType.ACADEMY_CHECKIN_DETECTED if payload["tipo"] == "checkin" else EventType.ACADEMY_ABSENCE_DETECTED,
                    occurred_at=datetime.fromisoformat(payload["data_hora"]),
                    external_event_id=str(payload["evento_id"]),
                    raw=payload,
                )

    e seria registrado no dicionário `CONNECTORS` em `app/integrations/academy/routes.py`.
    """

    provider: str

    def parse_webhook_event(self, payload: dict) -> NormalizedAcademyEvent: ...
