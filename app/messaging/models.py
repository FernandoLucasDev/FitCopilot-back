from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class SuggestedMessage(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "suggested_messages"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    summary_id: Mapped[str | None] = mapped_column(db.ForeignKey("student_daily_summaries.id"))
    insight_id: Mapped[str | None] = mapped_column(db.ForeignKey("ai_insights.id"))
    message_category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    subject_hint: Mapped[str | None] = mapped_column(String(140))
    message_text: Mapped[str] = mapped_column(db.Text(), nullable=False)
    tone: Mapped[str] = mapped_column(String(30), nullable=False, default="professional_warm")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="suggested", index=True)
    edited_message_text: Mapped[str | None] = mapped_column(db.Text())
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    summary = relationship("StudentDailySummary", back_populates="suggested_messages")
