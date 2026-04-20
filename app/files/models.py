from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class StudentFile(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "student_files"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    uploaded_by_user_id: Mapped[str] = mapped_column(db.ForeignKey("users.id"), nullable=False)
    file_category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    file_url: Mapped[str] = mapped_column(db.Text(), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    extracted_text: Mapped[str | None] = mapped_column(db.Text())
    extracted_structured_json: Mapped[dict | None] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"))
    ai_summary: Mapped[str | None] = mapped_column(db.Text())
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    student = relationship("StudentProfile", back_populates="files")
