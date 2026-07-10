"""Add wearable connection, data point, and connect-challenge tables

Revision ID: 20260713_0012
Revises: 20260712_0011
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0012"
down_revision = "20260712_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wearable_connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("external_athlete_id", sa.String(length=80), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String(length=200), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_wearable_connections_account_id_accounts")),
        sa.ForeignKeyConstraint(
            ["student_id"], ["student_profiles.id"], name=op.f("fk_wearable_connections_student_id_student_profiles")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wearable_connections")),
        sa.UniqueConstraint("student_id", "source", name="uq_wearable_connection_student_source"),
    )
    op.create_index(op.f("ix_wearable_connections_account_id"), "wearable_connections", ["account_id"], unique=False)
    op.create_index(op.f("ix_wearable_connections_source"), "wearable_connections", ["source"], unique=False)
    op.create_index(op.f("ix_wearable_connections_student_id"), "wearable_connections", ["student_id"], unique=False)

    op.create_table(
        "wearable_data_points",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("metric_type", sa.String(length=30), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_id", sa.String(length=80), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_wearable_data_points_account_id_accounts")),
        sa.ForeignKeyConstraint(
            ["student_id"], ["student_profiles.id"], name=op.f("fk_wearable_data_points_student_id_student_profiles")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wearable_data_points")),
        sa.UniqueConstraint("student_id", "source", "external_id", name="uq_wearable_point_dedup"),
    )
    op.create_index(op.f("ix_wearable_data_points_account_id"), "wearable_data_points", ["account_id"], unique=False)
    op.create_index(op.f("ix_wearable_data_points_metric_type"), "wearable_data_points", ["metric_type"], unique=False)
    op.create_index(op.f("ix_wearable_data_points_recorded_at"), "wearable_data_points", ["recorded_at"], unique=False)
    op.create_index(op.f("ix_wearable_data_points_source"), "wearable_data_points", ["source"], unique=False)
    op.create_index(op.f("ix_wearable_data_points_student_id"), "wearable_data_points", ["student_id"], unique=False)

    op.create_table(
        "wearable_connect_challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("state_token", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["student_id"], ["student_profiles.id"], name=op.f("fk_wearable_connect_challenges_student_id_student_profiles")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wearable_connect_challenges")),
        sa.UniqueConstraint("state_token", name=op.f("uq_wearable_connect_challenges_state_token")),
    )
    op.create_index(op.f("ix_wearable_connect_challenges_state_token"), "wearable_connect_challenges", ["state_token"], unique=True)
    op.create_index(op.f("ix_wearable_connect_challenges_student_id"), "wearable_connect_challenges", ["student_id"], unique=False)


def downgrade() -> None:
    op.drop_table("wearable_connect_challenges")
    op.drop_table("wearable_data_points")
    op.drop_table("wearable_connections")
