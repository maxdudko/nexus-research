"""SQLAlchemy declarative registry."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared metadata for Alembic and the ORM."""
