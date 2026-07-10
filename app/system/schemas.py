from __future__ import annotations

from app.common.schemas.base import ApiSchema


class CreateIncidentInput(ApiSchema):
    title: str
    severity: str = "minor"
    notes: str | None = None
