"""Add daily_calorie_target to student_profiles

Revision ID: 20260709_0006
Revises: 20260709_0005
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260709_0006"
down_revision = "20260709_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "student_profiles",
        sa.Column("daily_calorie_target", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("student_profiles", "daily_calorie_target")
