"""Shared test helpers."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from nexus.api.deps import CurrentUser, get_current_user
from nexus.core.config import Settings

PRINCIPAL = CurrentUser(
    id=UUID("11111111-1111-4111-8111-111111111111"),
    email="ada@example.com",
    role="user",
    session_id=UUID("22222222-2222-4222-8222-222222222222"),
)


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


def signed_in(app: FastAPI, user: CurrentUser = PRINCIPAL) -> FastAPI:
    """Treat every private route as this caller. Anonymous tests skip this."""
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.fixture
def stub_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit routes off PostgreSQL when they mint or look up a scope."""

    async def allow(
        factory: object,
        owner_id: UUID,
        research_id: UUID,
    ) -> UUID:
        del factory, owner_id
        return research_id

    async def mint(factory: object, owner_id: UUID, now: object) -> UUID:
        del factory, owner_id, now
        return UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    monkeypatch.setattr("nexus.api.ownership.owned_scope", allow)
    monkeypatch.setattr("nexus.api.ownership.mint_scope", mint)
    monkeypatch.setattr("nexus.ingestion.service.owned_scope", allow)
    monkeypatch.setattr("nexus.retrieval.service.owned_scope", allow)


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
