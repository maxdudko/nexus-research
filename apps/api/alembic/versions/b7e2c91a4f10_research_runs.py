"""research runs

Revision ID: b7e2c91a4f10
Revises: 6f509f423779
Create Date: 2026-10-18 13:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e2c91a4f10"
down_revision: str | Sequence[str] | None = "6f509f423779"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "research_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("research_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'partial', "
            "'insufficient_evidence', 'failed', 'interrupted')",
            name="ck_research_runs_status",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'",
            name="ck_research_runs_error_code",
        ),
        sa.CheckConstraint(
            "char_length(question) BETWEEN 10 AND 2000",
            name="ck_research_runs_question_length",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND error_code IS NULL) "
            "OR (status IN ('completed', 'partial', 'insufficient_evidence') "
            "AND finished_at IS NOT NULL AND error_code IS NULL) "
            "OR (status IN ('failed', 'interrupted') "
            "AND finished_at IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_research_runs_terminal",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_research_runs_created_at_run_id",
        "research_runs",
        [sa.text("created_at DESC"), sa.text("run_id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_research_runs_research_id_created_at",
        "research_runs",
        ["research_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "research_reports",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("research_reports")
    op.drop_index(
        "ix_research_runs_research_id_created_at",
        table_name="research_runs",
    )
    op.drop_index(
        "ix_research_runs_created_at_run_id",
        table_name="research_runs",
    )
    op.drop_table("research_runs")
