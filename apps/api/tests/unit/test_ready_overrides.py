"""Readiness failures injected through dependency overrides."""

from typing import Literal

import pytest
from tests.conftest import client_for, make_settings

from nexus.api.v1.health import ReadyStatus, get_ready_status
from nexus.main import create_app

Check = Literal["ok", "unavailable"]


@pytest.mark.parametrize(
    ("postgres", "qdrant"),
    [("unavailable", "ok"), ("ok", "unavailable")],
)
async def test_ready_reports_simulated_failure(postgres: Check, qdrant: Check) -> None:
    app = create_app(make_settings())

    async def override() -> ReadyStatus:
        return ReadyStatus(
            status="unavailable",
            postgres=postgres,
            qdrant=qdrant,
        )

    app.dependency_overrides[get_ready_status] = override
    async with client_for(app) as client:
        ready = await client.get("/api/v1/health/ready")
        live = await client.get("/api/v1/health/live")

    body = ready.json()
    assert ready.status_code == 503
    assert set(body) == {"status", "postgres", "qdrant"}
    assert set(body.values()) <= {"ok", "unavailable"}
    assert body == {
        "status": "unavailable",
        "postgres": postgres,
        "qdrant": qdrant,
    }
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
