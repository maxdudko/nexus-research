"""Unavailable Postgres and Qdrant, without stopping containers."""

import time

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.conftest import client_for, make_settings

from nexus.infrastructure.database import postgres_is_ready
from nexus.infrastructure.qdrant import qdrant_is_ready
from nexus.main import create_app

_CLOSED_PORT = 1
_POSTGRES_URL = f"postgresql+asyncpg://nexus:nexus@127.0.0.1:{_CLOSED_PORT}/unused"
_QDRANT_URL = f"http://127.0.0.1:{_CLOSED_PORT}"


async def test_postgres_ready_is_false_when_nothing_is_listening() -> None:
    started = time.monotonic()
    ready = await postgres_is_ready(_POSTGRES_URL, timeout_seconds=2.0)
    elapsed = time.monotonic() - started

    assert ready is False
    assert elapsed < 1.0


async def test_qdrant_ready_is_false_when_nothing_is_listening() -> None:
    started = time.monotonic()
    ready = await qdrant_is_ready(_QDRANT_URL, timeout_seconds=2.0)
    elapsed = time.monotonic() - started

    assert ready is False
    assert elapsed < 1.0


async def test_postgres_ready_returns_false_when_dispose_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(self: AsyncEngine, close: bool = True) -> None:
        raise RuntimeError("dispose failed")

    monkeypatch.setattr(AsyncEngine, "dispose", boom)
    ready = await postgres_is_ready(_POSTGRES_URL, timeout_seconds=1.0)

    assert ready is False


async def test_live_stays_ok_when_dependencies_are_unreachable() -> None:
    app = create_app(
        make_settings(
            database_url=_POSTGRES_URL,
            qdrant_url=_QDRANT_URL,
            readiness_timeout_seconds=1.0,
        ),
    )
    async with client_for(app) as client:
        live = await client.get("/api/v1/health/live")
        ready = await client.get("/api/v1/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "unavailable",
        "postgres": "unavailable",
        "qdrant": "unavailable",
    }
