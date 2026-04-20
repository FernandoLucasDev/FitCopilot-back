from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "users"

    account_id: Mapped[str | None] = mapped_column(db.ForeignKey("accounts.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    avatar_url: Mapped[str | None] = mapped_column(db.Text())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    external_user_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    core_access_token: Mapped[str | None] = mapped_column(db.Text())
    core_refresh_token: Mapped[str | None] = mapped_column(db.Text())

    account = relationship("Account", back_populates="users")
    professional_profile = relationship("ProfessionalProfile", back_populates="user", uselist=False)
    student_profile = relationship("StudentProfile", back_populates="user", uselist=False)
