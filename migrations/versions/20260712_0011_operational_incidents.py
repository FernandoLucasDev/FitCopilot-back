"""Add operational_incidents table

Revision ID: 20260712_0011
Revises: 20260712_0010
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260712_0011"
down_revision = "20260712_0010"
branch_labels = None
depends_on = None


GUID = postgresql.UUID(as_uuid=True).with_variant(sa.String(length=36), "sqlite")


def upgrade() -> None:
    op.create_table(
        "operational_incidents",
        sa.Column("id", GUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", GUID, nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="minor"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_operational_incidents_account_id_accounts")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operational_incidents")),
    )
    op.create_index(op.f("ix_operational_incidents_account_id"), "operational_incidents", ["account_id"], unique=False)
    op.create_index(op.f("ix_operational_incidents_severity"), "operational_incidents", ["severity"], unique=False)
    op.create_index(op.f("ix_operational_incidents_status"), "operational_incidents", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("operational_incidents")
