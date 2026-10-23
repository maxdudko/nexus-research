"""One research scope and its single owner.

Runs and sources point here. Reports stay attached to a run, and Qdrant
payloads are not an ownership source.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from nexus.models.base import Base


class ResearchScope(Base):
    """The authorization root for one ``research_id``."""

    __tablename__ = "research_scopes"
    __table_args__ = (Index("ix_research_scopes_owner_user_id", "owner_user_id"),)

    research_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_research_scopes_owner_user_id",
            ondelete="RESTRICT",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
