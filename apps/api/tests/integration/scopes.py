"""Quarantine scopes for tests that write runs or sources directly."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.models.scope import ResearchScope
from nexus.models.user import QUARANTINE_USER_ID


async def quarantine_scopes(
    factory: async_sessionmaker[AsyncSession],
    *research_ids: uuid.UUID,
) -> None:
    """Give legacy fixtures an owner. Production requests must not do this."""
    now = datetime.now(UTC)
    async with factory() as db, db.begin():
        for research_id in research_ids:
            if await db.get(ResearchScope, research_id) is None:
                db.add(
                    ResearchScope(
                        research_id=research_id,
                        owner_user_id=QUARANTINE_USER_ID,
                        created_at=now,
                        updated_at=now,
                    )
                )
