"""Async SQLAlchemy engine and request-scoped sessions."""

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)


def create_db_engine(
    url: str,
    *,
    null_pool: bool = False,
    connect_timeout: float | None = None,
) -> AsyncEngine:
    """Create a lazy async engine. Passing null_pool avoids the application pool."""
    connect_args: dict[str, float] = {}
    if connect_timeout is not None:
        connect_args["timeout"] = connect_timeout
        connect_args["command_timeout"] = connect_timeout
    if null_pool:
        return create_async_engine(
            url,
            pool_pre_ping=True,
            poolclass=NullPool,
            connect_args=connect_args,
        )
    return create_async_engine(url, pool_pre_ping=True, connect_args=connect_args)


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


async def postgres_is_ready(url: str, timeout_seconds: float) -> bool:
    """Return whether PostgreSQL accepts a bounded SELECT 1."""
    engine: AsyncEngine | None = None
    try:
        engine = create_db_engine(
            url,
            null_pool=True,
            connect_timeout=timeout_seconds,
        )
        async with asyncio.timeout(timeout_seconds):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("postgres readiness check failed: %s", type(exc).__name__)
        return False
    else:
        return True
    finally:
        if engine is not None:
            await engine.dispose()
