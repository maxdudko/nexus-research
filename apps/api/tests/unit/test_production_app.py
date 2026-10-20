"""Release mode serves the API without interactive documentation."""

import pytest
from tests.conftest import client_for, make_settings

from nexus.main import create_app

_RELEASE_URL = "postgresql+asyncpg://nexus:3f9a0c1e7b2d4a6f@127.0.0.1:9/unused"


@pytest.mark.parametrize("environment", ["production", "test"])
async def test_docs_routes_are_disabled(environment: str) -> None:
    app = create_app(make_settings(environment=environment, database_url=_RELEASE_URL))
    async with client_for(app) as client:
        live = await client.get("/api/v1/health/live")
        docs = await client.get("/docs")
        openapi = await client.get("/openapi.json")
        redoc = await client.get("/redoc")

    assert live.status_code == 200
    assert docs.status_code == 404
    assert openapi.status_code == 404
    assert redoc.status_code == 404
