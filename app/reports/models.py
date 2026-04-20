from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class GeneratedReport(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "generated_reports"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    requested_by_user_id: Mapped[str] = mapped_column(db.ForeignKey("users.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    period_start: Mapped[date | None] = mapped_column(Date())
    period_end: Mapped[date | None] = mapped_column(Date())
    summary_text: Mapped[str | None] = mapped_column(db.Text())
    storage_key: Mapped[str | None] = mapped_column(String(255))
    file_url: Mapped[str | None] = mapped_column(db.Text())
    metadata_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    student = relationship("StudentProfile", back_populates="reports")
