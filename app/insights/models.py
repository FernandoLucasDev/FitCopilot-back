from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class AIInsight(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "ai_insights"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str | None] = mapped_column(db.ForeignKey("student_profiles.id"), index=True)
    summary_id: Mapped[str | None] = mapped_column(db.ForeignKey("student_daily_summaries.id"))
    insight_scope: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    insight_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(db.Text(), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    action_label: Mapped[str | None] = mapped_column(String(80))
    action_payload_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    generated_by: Mapped[str] = mapped_column(String(20), nullable=False, default="ai")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    summary = relationship("StudentDailySummary", back_populates="insights")
