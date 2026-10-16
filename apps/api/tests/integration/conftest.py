"""Isolated Postgres database for integration tests."""

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from nexus.core.config import get_settings

TEST_DATABASE = "nexus_test"
API_ROOT = Path(__file__).resolve().parents[2]
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_PROTECTED_DATABASES = frozenset({"nexus_test", "postgres", "template0", "template1"})
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def require_local_test_database(configured: URL, name: str) -> None:
    """Refuse destructive test SQL unless the target is local ``nexus_test``.

    Call this before opening a connection. PostgreSQL identifiers cannot be
    query parameters, so ``DROP DATABASE`` would otherwise copy ``name`` into
    the SQL string. The error names the host and database only, never the URL,
    because the URL contains the password.
    """
    host = configured.host
    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"Refusing to drop a database on host {host!r}. "
            "Integration tests only recreate a database on localhost, "
            "127.0.0.1, or ::1."
        )
    if name != TEST_DATABASE or _IDENTIFIER.fullmatch(name) is None:
        raise RuntimeError(
            f"Refusing to drop database {name!r}. "
            "The only allowed test database name is 'nexus_test'."
        )
    configured_name = configured.database
    if configured_name in _PROTECTED_DATABASES:
        raise RuntimeError(
            "Refusing to recreate a test database when NEXUS_DATABASE_URL "
            f"uses database {configured_name!r}. Point it at the development "
            "database, not nexus_test, postgres, template0, or template1."
        )


def require_local_qdrant(url: str) -> None:
    """Refuse evidence tests that would change a non-local Qdrant."""
    host = urlsplit(url).hostname
    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"Refusing to modify Qdrant on host {host!r}. "
            "Evidence tests only use localhost, 127.0.0.1, or ::1."
        )


async def run_alembic(
    database_url: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    """Run Alembic against ``database_url`` without using the cached app settings."""
    env = os.environ.copy()
    env["NEXUS_DATABASE_URL"] = database_url
    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", *args],
        check=False,
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


async def _run_maintenance(configured: URL, name: str, *, create: bool) -> None:
    """Terminate and drop ``nexus_test``, creating it again when requested."""
    require_local_test_database(configured, name)
    maintenance = configured.set(database="postgres")
    quoted_name = f'"{name}"'
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
            await connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_name}"))
            if create:
                await connection.execute(text(f"CREATE DATABASE {quoted_name}"))
    finally:
        await engine.dispose()


@pytest.fixture
async def test_database_url() -> AsyncIterator[str]:
    """Create nexus_test and drop it after the test."""
    configured = make_url(str(get_settings().database_url))
    test_url = configured.set(database=TEST_DATABASE)
    await _run_maintenance(configured, TEST_DATABASE, create=True)
    try:
        yield test_url.render_as_string(hide_password=False)
    finally:
        await _run_maintenance(configured, TEST_DATABASE, create=False)
