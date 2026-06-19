from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class PhysicalAssessment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "physical_assessments"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(db.ForeignKey("users.id"), nullable=True, index=True)

    title: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(db.Text())
    assessment_date: Mapped[date] = mapped_column(Date(), nullable=False, index=True)

    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    bmi: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    body_fat_percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    lean_mass_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    fat_mass_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    basal_metabolic_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    visceral_fat_level: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    body_age: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    hydration_percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    chest_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    waist_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    abdomen_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    hip_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    left_arm_relaxed_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    right_arm_relaxed_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    left_arm_contracted_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    right_arm_contracted_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    left_forearm_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    right_forearm_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    left_thigh_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    right_thigh_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    left_calf_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    right_calf_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    neck_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    shoulders_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    resting_heart_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    blood_pressure: Mapped[str | None] = mapped_column(String(40))
    posture_notes: Mapped[str | None] = mapped_column(db.Text())
    mobility_notes: Mapped[str | None] = mapped_column(db.Text())
    injury_notes: Mapped[str | None] = mapped_column(db.Text())

    assessment_summary: Mapped[str | None] = mapped_column(db.Text())
    ai_summary: Mapped[str | None] = mapped_column(db.Text())
    ai_insights: Mapped[list] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=list)
    ai_recommendations: Mapped[list] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=list)

    photos = relationship("PhysicalAssessmentPhoto", back_populates="assessment", cascade="all, delete-orphan")
    ai_runs = relationship("PhysicalAssessmentAIRun", back_populates="assessment", cascade="all, delete-orphan")


class PhysicalAssessmentPhoto(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "physical_assessment_photos"

    assessment_id: Mapped[str] = mapped_column(db.ForeignKey("physical_assessments.id"), nullable=False, index=True)
    file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    file_url: Mapped[str | None] = mapped_column(String(1000))
    storage_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="local")
    photo_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    assessment = relationship("PhysicalAssessment", back_populates="photos")


class PhysicalAssessmentAIRun(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "physical_assessment_ai_runs"

    assessment_id: Mapped[str] = mapped_column(db.ForeignKey("physical_assessments.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(60), nullable=False, default="fitcopilot")
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="rules-v1")
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False, default="physical-assessment-v1")
    raw_response: Mapped[str | None] = mapped_column(db.Text())
    structured_output: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    assessment = relationship("PhysicalAssessment", back_populates="ai_runs")


class PhysicalAssessmentComparison(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "physical_assessment_comparisons"

    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    from_assessment_id: Mapped[str] = mapped_column(db.ForeignKey("physical_assessments.id"), nullable=False, index=True)
    to_assessment_id: Mapped[str] = mapped_column(db.ForeignKey("physical_assessments.id"), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(db.Text())
    changes_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
