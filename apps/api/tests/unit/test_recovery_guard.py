"""Anonymous recovery protection and uniform configuration failures."""

import pytest
from tests.conftest import client_for, make_settings

from nexus.main import create_app

ORIGIN = {"origin": "http://127.0.0.1:3000"}


@pytest.mark.parametrize(
    "path,body",
    [
        ("forgot-password", {"email": "ada@example.com"}),
        ("reset-password", {"token": "invalid", "new_password": "different-password"}),
    ],
)
@pytest.mark.parametrize("origin", [None, "https://evil.example"])
async def test_recovery_requires_origin(path, body, origin):
    app = create_app(make_settings())
    async with client_for(app) as client:
        response = await client.post(
            f"/api/v1/auth/{path}",
            json=body,
            headers={} if origin is None else {"origin": origin},
        )
    assert response.status_code == 403


async def test_unconfigured_recovery_is_uniform_even_with_stale_cookie():
    app = create_app(make_settings())
    async with client_for(app) as client:
        responses = [
            await client.post(
                "/api/v1/auth/forgot-password",
                json={"email": email},
                headers={**ORIGIN, "cookie": "nexus_session=stale"},
            )
            for email in ["ada@example.com", "unknown@example.com"]
        ]
    assert all(response.status_code == 503 for response in responses)
    assert responses[0].json() == responses[1].json()


async def test_recovery_limits_before_database(monkeypatch):
    from nexus.identity.errors import IdentityError

    calls = 0

    async def invalid(*args):
        nonlocal calls
        calls += 1
        raise IdentityError("invalid_reset_token")

    monkeypatch.setattr("nexus.api.v1.auth.consume_password_reset", invalid)
    app = create_app(make_settings())
    async with client_for(app) as client:
        for _ in range(5):
            response = await client.post(
                "/api/v1/auth/reset-password",
                json={"token": "bad", "new_password": "different-password"},
                headers={**ORIGIN, "cookie": "nexus_session=stale"},
            )
            assert response.status_code == 400
        limited = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": "bad", "new_password": "different-password"},
            headers=ORIGIN,
        )
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert calls == 5
