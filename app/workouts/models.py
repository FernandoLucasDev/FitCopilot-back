from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class WorkoutPlan(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "workout_plans"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(db.ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    objective: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(db.Text())
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    previous_version_id: Mapped[str | None] = mapped_column(db.ForeignKey("workout_plans.id"))
    valid_from: Mapped[date | None] = mapped_column(Date())
    valid_until: Mapped[date | None] = mapped_column(Date())
    ai_generated: Mapped[bool] = mapped_column(nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    student = relationship("StudentProfile", back_populates="workouts")
    days = relationship("WorkoutPlanDay", back_populates="workout_plan", cascade="all, delete-orphan")


class WorkoutPlanDay(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "workout_plan_days"

    workout_plan_id: Mapped[str] = mapped_column(db.ForeignKey("workout_plans.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(db.Text())

    workout_plan = relationship("WorkoutPlan", back_populates="days")
    exercises = relationship("WorkoutDayExercise", back_populates="workout_plan_day", cascade="all, delete-orphan")


class WorkoutDayExercise(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "workout_day_exercises"

    workout_plan_day_id: Mapped[str] = mapped_column(db.ForeignKey("workout_plan_days.id"), nullable=False, index=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    exercise_name: Mapped[str] = mapped_column(String(160), nullable=False)
    sets_count: Mapped[int | None] = mapped_column(Integer)
    reps_text: Mapped[str | None] = mapped_column(String(60))
    rest_seconds: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(db.Text())

    workout_plan_day = relationship("WorkoutPlanDay", back_populates="exercises")
