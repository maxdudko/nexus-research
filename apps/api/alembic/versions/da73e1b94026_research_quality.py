"""Persist research depth and applied server limits."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "da73e1b94026"
down_revision = "a4d9c2f78106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("depth", sa.Text(), nullable=False, server_default="standard"),
    )
    op.add_column(
        "research_runs",
        sa.Column(
            "applied_limits",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_research_runs_depth", "research_runs", "depth IN ('standard', 'deep')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_research_runs_depth", "research_runs", type_="check")
    op.drop_column("research_runs", "applied_limits")
    op.drop_column("research_runs", "depth")
