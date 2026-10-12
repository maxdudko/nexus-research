"""Async SQLAlchemy engine and request-scoped sessions."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def create_db_engine(url: str, *, null_pool: bool = False) -> AsyncEngine:
    """Create a lazy async engine. Passing null_pool avoids the application pool."""
    if null_pool:
        return create_async_engine(url, pool_pre_ping=True, poolclass=NullPool)
    return create_async_engine(url, pool_pre_ping=True)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a factory that opens one session per call."""
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one session for the request and close it afterwards.

    The handler owns the transaction. This dependency does not commit.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session
