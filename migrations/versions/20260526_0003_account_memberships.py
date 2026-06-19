"""add account memberships

Revision ID: 20260526_0003
Revises: 20260515_0002
Create Date: 2026-05-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260526_0003"
down_revision = "20260515_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("external_org_id", sa.String(length=80), nullable=True),
        sa.Column("external_member_id", sa.String(length=80), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("invited_email", sa.String(length=255), nullable=True),
        sa.Column("invited_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("invite_token", sa.String(length=160), nullable=True),
        sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("can_manage_billing", sa.Boolean(), nullable=False),
        sa.Column("permissions_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_account_memberships_account_id_accounts")),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], name=op.f("fk_account_memberships_invited_by_user_id_users")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_account_memberships_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_memberships")),
        sa.UniqueConstraint("account_id", "user_id", name="uq_account_memberships_account_user"),
        sa.UniqueConstraint("invite_token", name=op.f("uq_account_memberships_invite_token")),
    )
    op.create_index(op.f("ix_account_memberships_account_id"), "account_memberships", ["account_id"], unique=False)
    op.create_index(op.f("ix_account_memberships_deleted_at"), "account_memberships", ["deleted_at"], unique=False)
    op.create_index("ix_account_memberships_invite_token", "account_memberships", ["invite_token"], unique=False)
    op.create_index("ix_account_memberships_invited_email", "account_memberships", ["invited_email"], unique=False)
    op.create_index(op.f("ix_account_memberships_external_member_id"), "account_memberships", ["external_member_id"], unique=False)
    op.create_index(op.f("ix_account_memberships_external_org_id"), "account_memberships", ["external_org_id"], unique=False)
    op.create_index(op.f("ix_account_memberships_invited_by_user_id"), "account_memberships", ["invited_by_user_id"], unique=False)
    op.create_index(op.f("ix_account_memberships_role"), "account_memberships", ["role"], unique=False)
    op.create_index(op.f("ix_account_memberships_status"), "account_memberships", ["status"], unique=False)
    op.create_index(op.f("ix_account_memberships_user_id"), "account_memberships", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_account_memberships_user_id"), table_name="account_memberships")
    op.drop_index(op.f("ix_account_memberships_status"), table_name="account_memberships")
    op.drop_index(op.f("ix_account_memberships_role"), table_name="account_memberships")
    op.drop_index(op.f("ix_account_memberships_invited_by_user_id"), table_name="account_memberships")
    op.drop_index(op.f("ix_account_memberships_external_org_id"), table_name="account_memberships")
    op.drop_index(op.f("ix_account_memberships_external_member_id"), table_name="account_memberships")
    op.drop_index("ix_account_memberships_invited_email", table_name="account_memberships")
    op.drop_index("ix_account_memberships_invite_token", table_name="account_memberships")
    op.drop_index(op.f("ix_account_memberships_deleted_at"), table_name="account_memberships")
    op.drop_index(op.f("ix_account_memberships_account_id"), table_name="account_memberships")
    op.drop_table("account_memberships")
