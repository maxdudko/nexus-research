"""Liveness does not depend on infrastructure."""

from tests.conftest import client_for, make_settings

from nexus.main import create_app


async def test_live_and_openapi() -> None:
    app = create_app(make_settings())
    async with client_for(app) as client:
        live = await client.get("/api/v1/health/live")
        openapi = await client.get("/openapi.json")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert openapi.status_code == 200
