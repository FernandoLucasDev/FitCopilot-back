from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class OperationalIncident(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "operational_incidents"

    account_id: Mapped[str | None] = mapped_column(db.ForeignKey("accounts.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="minor", index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    notes: Mapped[str | None] = mapped_column(db.Text())
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
