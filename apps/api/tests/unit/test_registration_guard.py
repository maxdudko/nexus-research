"""Registration guards reject before hashing or database access."""

import pytest
from tests.conftest import client_for, make_settings

from nexus.main import create_app

ORIGIN = {"origin": "http://127.0.0.1:3000"}
BODY = {"email": "ada@example.com", "password": "correct-horse-1"}


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
async def test_registration_requires_trusted_origin(origin: str | None) -> None:
    app = create_app(make_settings())
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json=BODY,
            headers={} if origin is None else {"origin": origin},
        )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert app.state.limiter._registration_attempts == {}


async def test_invalid_attempts_count_and_stale_cookie_does_not_require_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_hash(password: str) -> str:
        raise AssertionError("registration reached hashing")

    monkeypatch.setattr("nexus.identity.store.hash_password", unexpected_hash)
    app = create_app(make_settings())
    async with client_for(app) as client:
        for index in range(5):
            response = await client.post(
                "/api/v1/auth/register",
                json={"email": "ada@example.com", "password": "short"},
                headers={
                    **ORIGIN,
                    "cookie": "nexus_session=stale",
                    "x-forwarded-for": f"203.0.113.{index}",
                },
            )
            assert response.status_code == 422
            assert "short" not in response.text
        limited = await client.post("/api/v1/auth/register", json=BODY, headers=ORIGIN)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    assert "set-cookie" not in limited.headers


@pytest.mark.parametrize(
    "body", [{}, {"email": [], "password": 123}, BODY | {"password": "x" * 129}]
)
async def test_registration_schema_failures_are_sanitized(body: dict) -> None:
    app = create_app(make_settings())
    async with client_for(app) as client:
        response = await client.post("/api/v1/auth/register", json=body, headers=ORIGIN)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "set-cookie" not in response.headers
