"""Isolated Postgres database for integration tests."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from nexus.core.config import get_settings

TEST_DATABASE = "nexus_test"


async def _recreate_database(maintenance: URL, name: str) -> None:
    engine = create_async_engine(
        maintenance.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


async def _drop_database(maintenance: URL, name: str) -> None:
    engine = create_async_engine(
        maintenance.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        await engine.dispose()


@pytest.fixture
async def test_database_url() -> AsyncIterator[str]:
    """Create nexus_test and drop it after the test."""
    configured = make_url(str(get_settings().database_url))
    maintenance = configured.set(database="postgres")
    test_url = configured.set(database=TEST_DATABASE)
    await _recreate_database(maintenance, TEST_DATABASE)
    try:
        yield test_url.render_as_string(hide_password=False)
    finally:
        await _drop_database(maintenance, TEST_DATABASE)
