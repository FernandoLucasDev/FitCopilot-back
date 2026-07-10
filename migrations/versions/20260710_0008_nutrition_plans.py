"""Add nutrition plan tables (structured meal plans)

Revision ID: 20260710_0008
Revises: 20260709_0007
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260710_0008"
down_revision = "20260709_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nutrition_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("objective", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("previous_version_id", sa.String(length=36), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_nutrition_plans_account_id_accounts")),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_nutrition_plans_created_by_user_id_users")),
        sa.ForeignKeyConstraint(["previous_version_id"], ["nutrition_plans.id"], name=op.f("fk_nutrition_plans_previous_version_id_nutrition_plans")),
        sa.ForeignKeyConstraint(["student_id"], ["student_profiles.id"], name=op.f("fk_nutrition_plans_student_id_student_profiles")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_nutrition_plans")),
    )
    op.create_index(op.f("ix_nutrition_plans_account_id"), "nutrition_plans", ["account_id"], unique=False)
    op.create_index(op.f("ix_nutrition_plans_student_id"), "nutrition_plans", ["student_id"], unique=False)

    op.create_table(
        "nutrition_plan_meals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("nutrition_plan_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["nutrition_plan_id"], ["nutrition_plans.id"], name=op.f("fk_nutrition_plan_meals_nutrition_plan_id_nutrition_plans")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_nutrition_plan_meals")),
    )
    op.create_index(op.f("ix_nutrition_plan_meals_nutrition_plan_id"), "nutrition_plan_meals", ["nutrition_plan_id"], unique=False)

    op.create_table(
        "nutrition_plan_food_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("nutrition_plan_meal_id", sa.String(length=36), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("food_name", sa.String(length=160), nullable=False),
        sa.Column("quantity_text", sa.String(length=80), nullable=True),
        sa.Column("calories", sa.Integer(), nullable=True),
        sa.Column("protein_grams", sa.Integer(), nullable=True),
        sa.Column("carbs_grams", sa.Integer(), nullable=True),
        sa.Column("fats_grams", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["nutrition_plan_meal_id"],
            ["nutrition_plan_meals.id"],
            name=op.f("fk_nutrition_plan_food_items_nutrition_plan_meal_id_nutrition_plan_meals"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_nutrition_plan_food_items")),
    )
    op.create_index(
        op.f("ix_nutrition_plan_food_items_nutrition_plan_meal_id"),
        "nutrition_plan_food_items",
        ["nutrition_plan_meal_id"],
        unique=False,
    )

    op.create_table(
        "student_nutrition_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("assigned_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.id"], name=op.f("fk_student_nutrition_plans_assigned_by_user_id_users")),
        sa.ForeignKeyConstraint(["plan_id"], ["nutrition_plans.id"], name=op.f("fk_student_nutrition_plans_plan_id_nutrition_plans")),
        sa.ForeignKeyConstraint(
            ["student_id"], ["student_profiles.id"], name=op.f("fk_student_nutrition_plans_student_id_student_profiles")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_student_nutrition_plans")),
    )
    op.create_index(op.f("ix_student_nutrition_plans_active"), "student_nutrition_plans", ["active"], unique=False)
    op.create_index(op.f("ix_student_nutrition_plans_plan_id"), "student_nutrition_plans", ["plan_id"], unique=False)
    op.create_index(op.f("ix_student_nutrition_plans_student_id"), "student_nutrition_plans", ["student_id"], unique=False)


def downgrade() -> None:
    op.drop_table("student_nutrition_plans")
    op.drop_table("nutrition_plan_food_items")
    op.drop_table("nutrition_plan_meals")
    op.drop_table("nutrition_plans")
