from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class ExternalSystemMapping(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """Vincula um aluno externo (de um sistema de academia) a um StudentProfile local."""

    __tablename__ = "external_system_mappings"
    __table_args__ = (
        UniqueConstraint("account_id", "provider", "external_student_id", name="uq_external_mapping_account_provider_external_id"),
    )

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    external_student_id: Mapped[str] = mapped_column(String(120), nullable=False)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)


class AcademyWebhookLog(UUIDPrimaryKeyMixin, db.Model):
    """Log append-only de eventos de webhook recebidos, para idempotência e observabilidade."""

    __tablename__ = "academy_webhook_logs"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_academy_webhook_provider_external_event_id"),
    )

    account_id: Mapped[str | None] = mapped_column(db.ForeignKey("accounts.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="processed", index=True)
    payload_json: Mapped[dict] = mapped_column(
        JSONB().with_variant(db.JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
