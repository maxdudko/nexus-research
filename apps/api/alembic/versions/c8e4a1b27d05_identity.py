"""identity

Revision ID: c8e4a1b27d05
Revises: b7e2c91a4f10
Create Date: 2026-10-22 16:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e4a1b27d05"
down_revision: str | Sequence[str] | None = "b7e2c91a4f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUARANTINE_USER_ID = "00000000-0000-4000-8000-000000000001"


def upgrade() -> None:
    """Add accounts and sessions. Research tables are unchanged."""
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "role IN ('user', 'admin')",
            name="ck_users_role",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'quarantine')",
            name="ck_users_status",
        ),
        sa.CheckConstraint(
            "email IS NULL OR char_length(email) BETWEEN 1 AND 254",
            name="ck_users_email_length",
        ),
        sa.CheckConstraint(
            "status = 'quarantine' OR email IS NOT NULL",
            name="ck_users_email_required",
        ),
        sa.CheckConstraint(
            "status <> 'active' OR password_hash IS NOT NULL",
            name="ck_users_active_hash",
        ),
        sa.CheckConstraint(
            "status <> 'quarantine' OR "
            "(email IS NULL AND password_hash IS NULL AND role = 'user')",
            name="ck_users_quarantine",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_users_email",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32",
            name="ck_auth_sessions_token_hash",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_auth_sessions_expires_at",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_auth_sessions_revoked_at",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_auth_sessions_user_id_expires_at",
        "auth_sessions",
        ["user_id", "expires_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            "INSERT INTO users (id, role, status) "
            f"VALUES ('{_QUARANTINE_USER_ID}', 'user', 'quarantine')"
        )
    )


def downgrade() -> None:
    """Drop identity tables. Runs, reports, and sources stay."""
    op.drop_index(
        "ix_auth_sessions_user_id_expires_at",
        table_name="auth_sessions",
    )
    op.drop_table("auth_sessions")
    op.drop_index("uq_users_email", table_name="users")
    op.drop_table("users")
