"""Add professional_vertical to accounts

Revision ID: 20260709_0005
Revises: 20260618_0004
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260709_0005"
down_revision = "20260618_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "accounts",
        sa.Column(
            "professional_vertical",
            sa.String(length=30),
            nullable=False,
            server_default="personal_trainer",
        ),
    )


def downgrade():
    op.drop_column("accounts", "professional_vertical")
