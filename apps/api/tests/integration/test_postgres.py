"""Real PostgreSQL connectivity against the isolated test database."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from nexus.infrastructure.database import create_db_engine

API_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
async def test_migration_and_select(test_database_url: str) -> None:
    env = os.environ.copy()
    env["NEXUS_DATABASE_URL"] = test_database_url
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=False,
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(text("SELECT 1"))
    finally:
        await engine.dispose()

    assert value == 1
