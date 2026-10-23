"""research scopes

Revision ID: e3b7d91c4a20
Revises: c8e4a1b27d05
Create Date: 2026-10-22 18:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3b7d91c4a20"
down_revision: str | Sequence[str] | None = "c8e4a1b27d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUARANTINE_USER_ID = "00000000-0000-4000-8000-000000000001"


def upgrade() -> None:
    """Attach existing research ids to the quarantine user. Qdrant is untouched."""
    op.create_table(
        "research_scopes",
        sa.Column("research_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_research_scopes_owner_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("research_id"),
    )
    op.create_index(
        "ix_research_scopes_owner_user_id",
        "research_scopes",
        ["owner_user_id"],
        unique=False,
    )
    op.execute(
        sa.text(
            "INSERT INTO research_scopes (research_id, owner_user_id) "
            "SELECT DISTINCT research_id, "
            f"'{_QUARANTINE_USER_ID}'::uuid FROM ("
            "SELECT research_id FROM research_runs "
            "UNION "
            "SELECT research_id FROM sources"
            ") AS legacy"
        )
    )
    op.create_foreign_key(
        "fk_research_runs_research_id",
        "research_runs",
        "research_scopes",
        ["research_id"],
        ["research_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_sources_research_id",
        "sources",
        "research_scopes",
        ["research_id"],
        ["research_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Drop scope ownership only while every scope is still quarantined."""
    connection = op.get_bind()
    owned = connection.execute(
        sa.text(
            "SELECT count(*) FROM research_scopes "
            f"WHERE owner_user_id <> '{_QUARANTINE_USER_ID}'::uuid"
        )
    ).scalar_one()
    if owned:
        raise RuntimeError("refusing to drop research_scopes while a real owner exists")
    op.drop_constraint(
        "fk_research_runs_research_id",
        "research_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_sources_research_id",
        "sources",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_research_scopes_owner_user_id",
        table_name="research_scopes",
    )
    op.drop_table("research_scopes")
