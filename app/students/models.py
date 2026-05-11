from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class StudentProfile(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "student_profiles"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    primary_professional_id: Mapped[str] = mapped_column(
        db.ForeignKey("professional_profiles.id"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(db.ForeignKey("users.id"), unique=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(30))
    birth_date: Mapped[date | None] = mapped_column(Date())
    sex: Mapped[str | None] = mapped_column(String(20))
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    current_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    goal_type: Mapped[str | None] = mapped_column(String(40))
    main_objective_text: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    adherence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=70, index=True)
    adherence_trend: Mapped[str] = mapped_column(String(20), nullable=False, default="stable")
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_signal_summary: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(db.Text())
    tags_json: Mapped[list] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account = relationship("Account", back_populates="students")
    primary_professional = relationship("ProfessionalProfile", back_populates="students")
    user = relationship("User", back_populates="student_profile")
    health_context = relationship("StudentHealthContext", back_populates="student", uselist=False)
    files = relationship("StudentFile", back_populates="student")
    summaries = relationship("StudentDailySummary", back_populates="student")
    daily_signals = relationship("StudentDailySignal", back_populates="student")
    interactions = relationship("StudentInteraction", back_populates="student")
    workouts = relationship("WorkoutPlan", back_populates="student")
    workout_assignments = relationship("StudentWorkout", back_populates="student")
    workout_sessions = relationship("WorkoutSession", back_populates="student")
    reports = relationship("GeneratedReport", back_populates="student")


class StudentHealthContext(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "student_health_contexts"

    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, unique=True, index=True)
    medical_notes: Mapped[str | None] = mapped_column(db.Text())
    limitations: Mapped[str | None] = mapped_column(db.Text())
    injuries_history: Mapped[str | None] = mapped_column(db.Text())
    medications: Mapped[str | None] = mapped_column(db.Text())
    allergies: Mapped[str | None] = mapped_column(db.Text())
    dietary_notes: Mapped[str | None] = mapped_column(db.Text())
    sleep_notes: Mapped[str | None] = mapped_column(db.Text())
    hydration_notes: Mapped[str | None] = mapped_column(db.Text())
    stress_notes: Mapped[str | None] = mapped_column(db.Text())
    contraindications: Mapped[str | None] = mapped_column(db.Text())

    student = relationship("StudentProfile", back_populates="health_context")


class StudentDailySignal(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "student_daily_signals"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    signal_date: Mapped[date] = mapped_column(Date(), nullable=False, index=True)
    signal_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    body: Mapped[str | None] = mapped_column(db.Text())
    payload_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    created_by_user_id: Mapped[str | None] = mapped_column(db.ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    student = relationship("StudentProfile", back_populates="daily_signals")


class StudentDailySummary(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "student_daily_summaries"
    __table_args__ = (UniqueConstraint("student_id", "summary_date", name="uq_student_daily_summary_student_date"),)

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    summary_date: Mapped[date] = mapped_column(Date(), nullable=False, index=True)
    food_summary_text: Mapped[str | None] = mapped_column(db.Text())
    activity_summary_text: Mapped[str | None] = mapped_column(db.Text())
    overall_summary_text: Mapped[str | None] = mapped_column(db.Text())
    ai_reading_text: Mapped[str | None] = mapped_column(db.Text())
    suggested_adjustment_text: Mapped[str | None] = mapped_column(db.Text())
    suggested_message_text: Mapped[str | None] = mapped_column(db.Text())
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="normal", index=True)
    needs_attention: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    was_generated_by_ai: Mapped[bool] = mapped_column(nullable=False, default=False)
    generation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    student = relationship("StudentProfile", back_populates="summaries")
    insights = relationship("AIInsight", back_populates="summary")
    suggested_messages = relationship("SuggestedMessage", back_populates="summary")


class StudentInteraction(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "student_interactions"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    interaction_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str | None] = mapped_column(db.Text())
    related_message_id: Mapped[str | None] = mapped_column(db.ForeignKey("suggested_messages.id"))
    created_by_user_id: Mapped[str | None] = mapped_column(db.ForeignKey("users.id"))
    interaction_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    student = relationship("StudentProfile", back_populates="interactions")
