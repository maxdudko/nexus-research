"""initial

Revision ID: 79d871cb4dad
Revises:
Create Date: 2026-10-12 18:29:39.118880

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "79d871cb4dad"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
