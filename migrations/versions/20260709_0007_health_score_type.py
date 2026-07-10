"""Add score_type to student_health_scores and widen unique constraint

Revision ID: 20260709_0007
Revises: 20260709_0006
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260709_0007"
down_revision = "20260709_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "student_health_scores",
        sa.Column("score_type", sa.String(length=20), nullable=False, server_default="operational"),
    )

    # O nome da constraint original não é garantido (a tabela foi criada fora desta
    # cadeia de migrations) — descobrimos o nome real via inspector em vez de assumir
    # "uq_student_health_score_student_date", para não quebrar o deploy se o nome
    # divergir entre ambientes.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_constraints = inspector.get_unique_constraints("student_health_scores")
    target_columns = {"student_id", "score_date"}
    for constraint in existing_constraints:
        if set(constraint["column_names"]) == target_columns and constraint["name"]:
            op.drop_constraint(constraint["name"], "student_health_scores", type_="unique")

    op.create_unique_constraint(
        "uq_student_health_score_student_date_type",
        "student_health_scores",
        ["student_id", "score_date", "score_type"],
    )


def downgrade():
    op.drop_constraint("uq_student_health_score_student_date_type", "student_health_scores", type_="unique")
    op.create_unique_constraint(
        "uq_student_health_score_student_date",
        "student_health_scores",
        ["student_id", "score_date"],
    )
    op.drop_column("student_health_scores", "score_type")
