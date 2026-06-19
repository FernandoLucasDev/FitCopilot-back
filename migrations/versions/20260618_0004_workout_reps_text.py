"""Expand workout exercise reps text

Revision ID: 20260618_0004
Revises: 20260526_0003
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa


revision = "20260618_0004"
down_revision = "20260526_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "workout_day_exercises",
        "reps_text",
        existing_type=sa.String(length=60),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "workout_day_exercises",
        "reps_text",
        existing_type=sa.Text(),
        type_=sa.String(length=60),
        existing_nullable=True,
    )
