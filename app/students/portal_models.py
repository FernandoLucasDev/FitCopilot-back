from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class StudentLoginChallenge(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "student_login_challenges"

    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    otp_code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by_ip: Mapped[str | None] = mapped_column(String(64))
