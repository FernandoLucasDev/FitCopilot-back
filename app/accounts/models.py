from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class Account(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", index=True)
    timezone: Mapped[str] = mapped_column(String(60), nullable=False, default="America/Sao_Paulo")
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="pt-BR")
    current_plan_code: Mapped[str | None] = mapped_column(String(40))
    max_students: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    monthly_ai_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    ai_credits_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settings_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    external_org_id: Mapped[str | None] = mapped_column(String(64), index=True)

    users = relationship("User", back_populates="account")
    professionals = relationship("ProfessionalProfile", back_populates="account")
    students = relationship("StudentProfile", back_populates="account")


class ProfessionalProfile(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "professional_profiles"

    user_id: Mapped[str] = mapped_column(db.ForeignKey("users.id"), nullable=False, unique=True, index=True)
    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    professional_type: Mapped[str] = mapped_column(String(30), nullable=False)
    bio: Mapped[str | None] = mapped_column(db.Text())
    registration_number: Mapped[str | None] = mapped_column(String(60))
    onboarding_completed: Mapped[bool] = mapped_column(nullable=False, default=False)
    can_manage_students: Mapped[bool] = mapped_column(nullable=False, default=True)
    can_manage_workouts: Mapped[bool] = mapped_column(nullable=False, default=True)
    can_upload_evaluations: Mapped[bool] = mapped_column(nullable=False, default=True)

    account = relationship("Account", back_populates="professionals")
    user = relationship("User", back_populates="professional_profile")
    students = relationship("StudentProfile", back_populates="primary_professional")
