"""Add academy integration tables (external mapping + webhook log)

Revision ID: 20260712_0010
Revises: 20260711_0009
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260712_0010"
down_revision = "20260711_0009"
branch_labels = None
depends_on = None


GUID = postgresql.UUID(as_uuid=True).with_variant(sa.String(length=36), "sqlite")


def upgrade() -> None:
    op.create_table(
        "external_system_mappings",
        sa.Column("id", GUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", GUID, nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_student_id", sa.String(length=120), nullable=False),
        sa.Column("student_id", GUID, nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_external_system_mappings_account_id_accounts")),
        sa.ForeignKeyConstraint(
            ["student_id"], ["student_profiles.id"], name=op.f("fk_external_system_mappings_student_id_student_profiles")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_system_mappings")),
        sa.UniqueConstraint(
            "account_id", "provider", "external_student_id", name="uq_external_mapping_account_provider_external_id"
        ),
    )
    op.create_index(op.f("ix_external_system_mappings_account_id"), "external_system_mappings", ["account_id"], unique=False)
    op.create_index(op.f("ix_external_system_mappings_provider"), "external_system_mappings", ["provider"], unique=False)
    op.create_index(op.f("ix_external_system_mappings_student_id"), "external_system_mappings", ["student_id"], unique=False)

    op.create_table(
        "academy_webhook_logs",
        sa.Column("id", GUID, nullable=False),
        sa.Column("account_id", GUID, nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_event_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="processed"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_academy_webhook_logs_account_id_accounts")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academy_webhook_logs")),
        sa.UniqueConstraint("provider", "external_event_id", name="uq_academy_webhook_provider_external_event_id"),
    )
    op.create_index(op.f("ix_academy_webhook_logs_account_id"), "academy_webhook_logs", ["account_id"], unique=False)
    op.create_index(op.f("ix_academy_webhook_logs_provider"), "academy_webhook_logs", ["provider"], unique=False)
    op.create_index(op.f("ix_academy_webhook_logs_status"), "academy_webhook_logs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("academy_webhook_logs")
    op.drop_table("external_system_mappings")
