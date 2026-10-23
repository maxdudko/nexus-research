"""Research scope ownership. Callers supply the principal; ids are never trusted."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.identity.errors import IdentityError
from nexus.models.scope import ResearchScope
from nexus.models.user import QUARANTINE_USER_ID, User
from nexus.observability.logging import log_event


async def mint_scope(
    factory: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    now: datetime,
) -> uuid.UUID:
    """Insert one server-generated scope for ``owner_id``."""
    research_id = uuid.uuid4()
    async with factory() as db, db.begin():
        db.add(
            ResearchScope(
                research_id=research_id,
                owner_user_id=owner_id,
                created_at=now,
                updated_at=now,
            )
        )
    return research_id


async def owned_scope(
    factory: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    research_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the id when this owner holds it. Never insert a missing scope."""
    async with factory() as db, db.begin():
        found = await db.scalar(
            select(ResearchScope.research_id).where(
                ResearchScope.research_id == research_id,
                ResearchScope.owner_user_id == owner_id,
            )
        )
    return found


async def adopt_scope(
    factory: async_sessionmaker[AsyncSession],
    research_id: uuid.UUID,
    destination_user_id: uuid.UUID,
    now: datetime,
) -> None:
    """Move a quarantined scope to a real account. Content is not read."""
    async with factory() as db, db.begin():
        scope = await db.scalar(
            select(ResearchScope)
            .where(ResearchScope.research_id == research_id)
            .with_for_update()
        )
        if scope is None:
            raise IdentityError("scope_not_found")
        if scope.owner_user_id != QUARANTINE_USER_ID:
            raise IdentityError("not_quarantined")
        destination = await db.get(User, destination_user_id)
        if destination is None:
            raise IdentityError("not_found")
        if destination.id == QUARANTINE_USER_ID or destination.status == "quarantine":
            raise IdentityError("quarantine")
        scope.owner_user_id = destination.id
        scope.updated_at = now
    log_event(
        "identity.audit",
        action="adopt_scope",
        target_user_id=str(destination_user_id),
        research_id=str(research_id),
    )
