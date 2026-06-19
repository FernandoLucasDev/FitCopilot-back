"""add physical assessments

Revision ID: 20260515_0002
Revises: 20260511_0001
Create Date: 2026-05-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260515_0002"
down_revision = "20260511_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "physical_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("assessment_date", sa.Date(), nullable=False),
        sa.Column("weight_kg", sa.Numeric(6, 2), nullable=True),
        sa.Column("height_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("bmi", sa.Numeric(5, 2), nullable=True),
        sa.Column("body_fat_percentage", sa.Numeric(5, 2), nullable=True),
        sa.Column("lean_mass_kg", sa.Numeric(6, 2), nullable=True),
        sa.Column("fat_mass_kg", sa.Numeric(6, 2), nullable=True),
        sa.Column("basal_metabolic_rate", sa.Numeric(8, 2), nullable=True),
        sa.Column("visceral_fat_level", sa.Numeric(5, 2), nullable=True),
        sa.Column("body_age", sa.Numeric(5, 2), nullable=True),
        sa.Column("hydration_percentage", sa.Numeric(5, 2), nullable=True),
        sa.Column("chest_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("waist_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("abdomen_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("hip_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("left_arm_relaxed_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("right_arm_relaxed_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("left_arm_contracted_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("right_arm_contracted_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("left_forearm_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("right_forearm_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("left_thigh_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("right_thigh_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("left_calf_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("right_calf_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("neck_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("shoulders_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("resting_heart_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("blood_pressure", sa.String(length=40), nullable=True),
        sa.Column("posture_notes", sa.Text(), nullable=True),
        sa.Column("mobility_notes", sa.Text(), nullable=True),
        sa.Column("injury_notes", sa.Text(), nullable=True),
        sa.Column("assessment_summary", sa.Text(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_insights", sa.JSON(), nullable=False),
        sa.Column("ai_recommendations", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["student_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_physical_assessments_account_id", "physical_assessments", ["account_id"])
    op.create_index("ix_physical_assessments_student_id", "physical_assessments", ["student_id"])
    op.create_index("ix_physical_assessments_created_by_user_id", "physical_assessments", ["created_by_user_id"])
    op.create_index("ix_physical_assessments_assessment_date", "physical_assessments", ["assessment_date"])

    op.create_table(
        "physical_assessment_photos",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("file_key", sa.String(length=500), nullable=False),
        sa.Column("file_url", sa.String(length=1000), nullable=True),
        sa.Column("storage_provider", sa.String(length=40), nullable=False),
        sa.Column("photo_type", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["physical_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_physical_assessment_photos_assessment_id", "physical_assessment_photos", ["assessment_id"])
    op.create_index("ix_physical_assessment_photos_photo_type", "physical_assessment_photos", ["photo_type"])

    op.create_table(
        "physical_assessment_ai_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("structured_output", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["physical_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_physical_assessment_ai_runs_assessment_id", "physical_assessment_ai_runs", ["assessment_id"])

    op.create_table(
        "physical_assessment_comparisons",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("from_assessment_id", sa.String(length=36), nullable=False),
        sa.Column("to_assessment_id", sa.String(length=36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("changes_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["from_assessment_id"], ["physical_assessments.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["student_profiles.id"]),
        sa.ForeignKeyConstraint(["to_assessment_id"], ["physical_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_physical_assessment_comparisons_student_id", "physical_assessment_comparisons", ["student_id"])
    op.create_index("ix_physical_assessment_comparisons_from_assessment_id", "physical_assessment_comparisons", ["from_assessment_id"])
    op.create_index("ix_physical_assessment_comparisons_to_assessment_id", "physical_assessment_comparisons", ["to_assessment_id"])


def downgrade() -> None:
    op.drop_index("ix_physical_assessment_comparisons_to_assessment_id", table_name="physical_assessment_comparisons")
    op.drop_index("ix_physical_assessment_comparisons_from_assessment_id", table_name="physical_assessment_comparisons")
    op.drop_index("ix_physical_assessment_comparisons_student_id", table_name="physical_assessment_comparisons")
    op.drop_table("physical_assessment_comparisons")
    op.drop_index("ix_physical_assessment_ai_runs_assessment_id", table_name="physical_assessment_ai_runs")
    op.drop_table("physical_assessment_ai_runs")
    op.drop_index("ix_physical_assessment_photos_photo_type", table_name="physical_assessment_photos")
    op.drop_index("ix_physical_assessment_photos_assessment_id", table_name="physical_assessment_photos")
    op.drop_table("physical_assessment_photos")
    op.drop_index("ix_physical_assessments_assessment_date", table_name="physical_assessments")
    op.drop_index("ix_physical_assessments_created_by_user_id", table_name="physical_assessments")
    op.drop_index("ix_physical_assessments_student_id", table_name="physical_assessments")
    op.drop_index("ix_physical_assessments_account_id", table_name="physical_assessments")
    op.drop_table("physical_assessments")
