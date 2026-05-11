"""add professional password reset challenges

Revision ID: 20260511_0001
Revises:
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260511_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "professional_password_reset_challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("otp_code_hash", sa.String(length=255), nullable=False),
        sa.Column("delivery_status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_ip", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_professional_password_reset_challenges_email", "professional_password_reset_challenges", ["email"])
    op.create_index("ix_professional_password_reset_challenges_user_id", "professional_password_reset_challenges", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_professional_password_reset_challenges_user_id", table_name="professional_password_reset_challenges")
    op.drop_index("ix_professional_password_reset_challenges_email", table_name="professional_password_reset_challenges")
    op.drop_table("professional_password_reset_challenges")
