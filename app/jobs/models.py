from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.common.db.types import GUID
from app.extensions import db


class BackgroundJob(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "background_jobs"

    account_id: Mapped[str | None] = mapped_column(db.ForeignKey("accounts.id"), index=True)
    student_id: Mapped[str | None] = mapped_column(db.ForeignKey("student_profiles.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(50))
    reference_id: Mapped[str | None] = mapped_column(GUID())
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    result_json: Mapped[dict | None] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"))
    error_message: Mapped[str | None] = mapped_column(db.Text())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "audit_logs"

    account_id: Mapped[str | None] = mapped_column(db.ForeignKey("accounts.id"), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(db.ForeignKey("users.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    old_values_json: Mapped[dict | None] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"))
    new_values_json: Mapped[dict | None] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
