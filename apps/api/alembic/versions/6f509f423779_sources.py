"""sources

Revision ID: 6f509f423779
Revises: 79d871cb4dad
Create Date: 2026-10-16 13:17:02.627739

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f509f423779"
down_revision: str | Sequence[str] | None = "79d871cb4dad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Enum("WEB", "PDF", name="source_type"), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("display_name", sa.String(length=300), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("indexing_content_hash", sa.String(length=64), nullable=True),
        sa.Column("indexing_token", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "INDEXING",
                "INDEXED",
                "FAILED",
                name="source_status",
            ),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "chunk_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
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
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sources_content_hash_hex",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'",
            name="ck_sources_error_code",
        ),
        sa.CheckConstraint(
            "indexing_content_hash IS NULL OR indexing_content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sources_indexing_hash_hex",
        ),
        sa.CheckConstraint(
            "status <> 'INDEXED' OR content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sources_indexed_hash",
        ),
        sa.CheckConstraint(
            "type <> 'PDF' OR uri = 'pdf:' || content_hash",
            name="ck_sources_pdf_uri",
        ),
        sa.CheckConstraint(
            "type <> 'WEB' OR uri NOT LIKE 'pdf:%'",
            name="ck_sources_web_uri",
        ),
        sa.CheckConstraint(
            "chunk_count >= 0",
            name="ck_sources_chunk_count_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sources_research_id"),
        "sources",
        ["research_id"],
        unique=False,
    )
    op.create_index(
        "uq_sources_pdf_identity",
        "sources",
        ["research_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("type = 'PDF'"),
    )
    op.create_index(
        "uq_sources_web_identity",
        "sources",
        ["research_id", "uri"],
        unique=True,
        postgresql_where=sa.text("type = 'WEB'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_sources_web_identity",
        table_name="sources",
        postgresql_where=sa.text("type = 'WEB'"),
    )
    op.drop_index(
        "uq_sources_pdf_identity",
        table_name="sources",
        postgresql_where=sa.text("type = 'PDF'"),
    )
    op.drop_index(op.f("ix_sources_research_id"), table_name="sources")
    op.drop_table("sources")
    sa.Enum(name="source_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="source_type").drop(op.get_bind(), checkfirst=True)
