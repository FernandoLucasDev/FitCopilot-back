from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
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
    professional_vertical: Mapped[str] = mapped_column(String(30), nullable=False, default="personal_trainer")
    max_students: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    monthly_ai_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    ai_credits_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settings_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    external_org_id: Mapped[str | None] = mapped_column(String(64), index=True)
    parent_account_id: Mapped[str | None] = mapped_column(db.ForeignKey("accounts.id"), index=True)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False, default="studio", index=True)
    brand_config: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    enterprise_contract_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)

    users = relationship("User", back_populates="account")
    professionals = relationship("ProfessionalProfile", back_populates="account")
    students = relationship("StudentProfile", back_populates="account")
    parent_account = relationship("Account", remote_side="Account.id", back_populates="child_accounts")
    child_accounts = relationship("Account", back_populates="parent_account")


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


class AccountMembership(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "account_memberships"
    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_account_memberships_account_user"),
        db.Index("ix_account_memberships_invite_token", "invite_token"),
        db.Index("ix_account_memberships_invited_email", "invited_email"),
    )

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(db.ForeignKey("users.id"), nullable=True, index=True)
    external_org_id: Mapped[str | None] = mapped_column(String(80), index=True)
    external_member_id: Mapped[str | None] = mapped_column(String(80), index=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="TRAINER", index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE", index=True)
    invited_email: Mapped[str | None] = mapped_column(String(255))
    invited_by_user_id: Mapped[str | None] = mapped_column(db.ForeignKey("users.id"), nullable=True, index=True)
    invite_token: Mapped[str | None] = mapped_column(String(160), unique=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    can_manage_billing: Mapped[bool] = mapped_column(nullable=False, default=False)
    permissions_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
