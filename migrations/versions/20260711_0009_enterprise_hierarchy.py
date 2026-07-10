"""Add enterprise hierarchy, branding and contract fields to accounts

Revision ID: 20260711_0009
Revises: 20260710_0008
Create Date: 2026-07-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260711_0009"
down_revision = "20260710_0008"
branch_labels = None
depends_on = None


GUID = postgresql.UUID(as_uuid=True).with_variant(sa.String(length=36), "sqlite")


def upgrade() -> None:
    op.add_column("accounts", sa.Column("parent_account_id", GUID, nullable=True))
    op.add_column("accounts", sa.Column("account_type", sa.String(length=20), nullable=False, server_default="studio"))
    op.add_column("accounts", sa.Column("brand_config", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("accounts", sa.Column("enterprise_contract_json", sa.JSON(), nullable=False, server_default="{}"))
    op.create_index(op.f("ix_accounts_parent_account_id"), "accounts", ["parent_account_id"], unique=False)
    op.create_index(op.f("ix_accounts_account_type"), "accounts", ["account_type"], unique=False)
    op.create_foreign_key(
        op.f("fk_accounts_parent_account_id_accounts"),
        "accounts",
        "accounts",
        ["parent_account_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_accounts_parent_account_id_accounts"), "accounts", type_="foreignkey")
    op.drop_index(op.f("ix_accounts_account_type"), table_name="accounts")
    op.drop_index(op.f("ix_accounts_parent_account_id"), table_name="accounts")
    op.drop_column("accounts", "enterprise_contract_json")
    op.drop_column("accounts", "brand_config")
    op.drop_column("accounts", "account_type")
    op.drop_column("accounts", "parent_account_id")
