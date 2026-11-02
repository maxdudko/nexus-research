"""Password change guards run before credential verification."""

import pytest
from tests.conftest import client_for, make_settings, signed_in

from nexus.identity.errors import IdentityError
from nexus.identity.tokens import csrf_token
from nexus.main import create_app

BODY = {
    "current_password": "current-password-1",
    "new_password": "different-password-2",
}
ORIGIN = {"origin": "http://127.0.0.1:3000"}


async def test_anonymous_password_change_is_rejected() -> None:
    app = create_app(make_settings())
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/change-password", json=BODY, headers=ORIGIN
        )
    assert response.status_code == 401
    assert app.state.limiter._password_changes == {}


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
async def test_origin_is_required_even_without_cookie(origin: str | None) -> None:
    app = signed_in(create_app(make_settings()))
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/change-password",
            json=BODY,
            headers={} if origin is None else {"origin": origin},
        )
    assert response.status_code == 403
    assert app.state.limiter._password_changes == {}


@pytest.mark.parametrize("token", [None, "forged"])
async def test_session_cookie_requires_valid_csrf(token: str | None) -> None:
    app = signed_in(create_app(make_settings()))
    headers = {**ORIGIN, "cookie": "nexus_session=token; nexus_csrf=forged"}
    if token is not None:
        headers["x-csrf-token"] = token
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/change-password", json=BODY, headers=headers
        )
    assert response.status_code == 403
    assert app.state.limiter._password_changes == {}


async def test_password_change_limited_before_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def wrong(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise IdentityError("current_password_incorrect")

    monkeypatch.setattr("nexus.api.v1.auth.change_password", wrong)
    settings = make_settings()
    app = signed_in(create_app(settings))
    token = csrf_token("session", settings.session_pepper.get_secret_value())
    headers = {
        **ORIGIN,
        "cookie": f"nexus_session=session; nexus_csrf={token}",
        "x-csrf-token": token,
    }
    async with client_for(app) as client:
        for _ in range(5):
            response = await client.post(
                "/api/v1/auth/change-password", json=BODY, headers=headers
            )
            assert response.status_code == 400
        response = await client.post(
            "/api/v1/auth/change-password", json=BODY, headers=headers
        )
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert calls == 5
    assert "set-cookie" not in response.headers
    assert BODY["current_password"] not in response.text
    assert BODY["new_password"] not in response.text
