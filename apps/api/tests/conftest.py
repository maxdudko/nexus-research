"""Shared test helpers."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from nexus.core.config import Settings


def pytest_configure() -> None:
    """Let imports of the app succeed when .env has no session pepper yet."""
    os.environ.setdefault(
        "NEXUS_SESSION_PEPPER",
        "test-session-pepper-0123456789abcdef",
    )


def make_settings(**overrides: object) -> Settings:
    """Build settings from arguments, ignoring apps/api/.env."""
    values: dict[str, object] = {
        "environment": "development",
        "host": "127.0.0.1",
        "port": 8000,
        "database_url": "postgresql+asyncpg://nexus:nexus@127.0.0.1:9/unused",
        "qdrant_url": "http://127.0.0.1:6333",
        "readiness_timeout_seconds": 2.0,
        "session_pepper": "test-session-pepper-0123456789abcdef",
        "trusted_origins": ["http://127.0.0.1:3000", "http://localhost:3000"],
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@asynccontextmanager
async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Serve the app with its lifespan running."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            yield client
