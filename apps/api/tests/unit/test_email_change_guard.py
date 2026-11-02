"""Email mutation authorization, CSRF, and independent attempt budgets."""

import pytest
from tests.conftest import client_for, make_settings, signed_in

from nexus.identity.errors import IdentityError
from nexus.identity.tokens import csrf_token
from nexus.main import create_app

BODY = {"new_email": "new@example.com", "current_password": "correct-horse-1"}
ORIGIN = {"origin": "http://127.0.0.1:3000"}


async def test_anonymous_email_change_is_rejected():
    app = create_app(make_settings())
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/change-email", json=BODY, headers=ORIGIN
        )
    assert response.status_code == 401
    assert app.state.limiter._email_changes == {}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"origin": "https://evil.example"},
        {**ORIGIN, "cookie": "nexus_session=token; nexus_csrf=wrong"},
        {
            **ORIGIN,
            "cookie": "nexus_session=token; nexus_csrf=wrong",
            "x-csrf-token": "wrong",
        },
    ],
)
async def test_email_change_origin_and_csrf(headers):
    app = signed_in(create_app(make_settings()))
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/change-email", json=BODY, headers=headers
        )
    assert response.status_code == 403
    assert app.state.limiter._email_changes == {}


async def test_email_limit_runs_before_password_verification(monkeypatch):
    calls = 0

    async def wrong(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise IdentityError("current_password_incorrect")

    monkeypatch.setattr("nexus.api.v1.auth.change_email", wrong)
    settings = make_settings()
    app = signed_in(create_app(settings))
    csrf = csrf_token("token", settings.session_pepper.get_secret_value())
    headers = {
        **ORIGIN,
        "cookie": f"nexus_session=token; nexus_csrf={csrf}",
        "x-csrf-token": csrf,
    }
    async with client_for(app) as client:
        for _ in range(5):
            response = await client.post(
                "/api/v1/auth/change-email", json=BODY, headers=headers
            )
            assert response.status_code == 400
        limited = await client.post(
            "/api/v1/auth/change-email", json=BODY, headers=headers
        )
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert calls == 5
