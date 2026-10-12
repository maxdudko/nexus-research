"""Readiness against the local Qdrant container and the test database."""

import pytest
from tests.conftest import client_for, make_settings

from nexus.main import create_app


@pytest.mark.integration
async def test_ready_with_local_services(test_database_url: str) -> None:
    app = create_app(
        make_settings(
            database_url=test_database_url,
            qdrant_url="http://127.0.0.1:6333",
        ),
    )
    async with client_for(app) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "postgres": "ok",
        "qdrant": "ok",
    }
