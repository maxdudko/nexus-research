"""Atomic self-service onboarding against PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from tests.conftest import client_for
from tests.integration.test_auth_api import ORIGIN, PASSWORD, _settings, _upgrade

from nexus.identity.errors import IdentityError
from nexus.identity.passwords import verify_password
from nexus.identity.store import disable_user, register_user
from nexus.identity.tokens import token_hash
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.main import create_app
from nexus.models.user import AuthSession, User

pytestmark = pytest.mark.integration


async def test_register_session_and_privileges(test_database_url: str) -> None:
    await _upgrade(test_database_url)
    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": " Ada@Example.com ",
                "password": PASSWORD,
                "role": "admin",
                "status": "disabled",
            },
            headers={**ORIGIN, "cookie": "nexus_session=stale"},
        )
        assert response.status_code == 201
        assert response.json()["email"] == "ada@example.com"
        assert response.json()["role"] == "user"
        assert set(response.json()) == {"id", "email", "role"}
        assert response.headers["cache-control"] == "no-store"
        cookies = response.headers.get_list("set-cookie")
        assert len(cookies) == 2
        assert "HttpOnly" in next(c for c in cookies if c.startswith("nexus_session="))
        assert "HttpOnly" not in next(c for c in cookies if c.startswith("nexus_csrf="))
        assert all("SameSite=lax" in c and "Path=/" in c for c in cookies)
        me = await client.get("/api/v1/auth/me")
        assert me.json() == response.json()
        assert (await client.get("/api/v1/admin/overview")).status_code == 403
        csrf = client.cookies.get("nexus_csrf") or ""
        headers = {**ORIGIN, "x-csrf-token": csrf}
        research = await client.post("/api/v1/research/scopes", headers=headers)
        assert research.status_code == 200
        async with app.state.session_factory() as db:
            user = await db.scalar(select(User).where(User.email == "ada@example.com"))
            assert user is not None and user.status == "active"
            assert user.password_hash != PASSWORD
            assert verify_password(PASSWORD, user.password_hash or "")
            stored = await db.scalar(
                select(AuthSession).where(AuthSession.user_id == user.id)
            )
            assert stored is not None
            raw_token = client.cookies.get("nexus_session") or ""
            assert stored.token_hash == token_hash(
                raw_token, app.state.settings.session_pepper.get_secret_value()
            )
        assert (
            await client.post("/api/v1/auth/logout", headers=headers)
        ).status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_duplicate_and_disabled_email(test_database_url: str) -> None:
    await _upgrade(test_database_url)
    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        body = {"email": "ada@example.com", "password": PASSWORD}
        assert (
            await client.post("/api/v1/auth/register", json=body, headers=ORIGIN)
        ).status_code == 201
        for disabled in (False, True):
            if disabled:
                await disable_user(
                    app.state.session_factory, body["email"], datetime.now(UTC)
                )
            response = await client.post(
                "/api/v1/auth/register",
                json=body | {"email": "ADA@example.com"},
                headers=ORIGIN,
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "email_unavailable"
            assert "set-cookie" not in response.headers
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(AuthSession)) == 1


async def test_registration_race_and_transaction_rollback(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    now = datetime.now(UTC)

    async def register(email: str, digest: bytes):
        return await register_user(
            factory, email, PASSWORD, digest, now, now + timedelta(hours=1)
        )

    try:
        results = await asyncio.gather(
            register("ada@example.com", b"a" * 32),
            register("ADA@example.com", b"b" * 32),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, IdentityError)]
        assert len(failures) == 1 and failures[0].code == "duplicate_email"
        assert sum(not isinstance(result, BaseException) for result in results) == 1
        with pytest.raises(IntegrityError):
            await register("rollback@example.com", b"invalid-length")
        async with factory() as db:
            assert (
                await db.scalar(
                    select(User).where(User.email == "rollback@example.com")
                )
                is None
            )
            assert await db.scalar(select(func.count()).select_from(AuthSession)) == 1
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "email,password",
    [
        ("bad-email", PASSWORD),
        ("ada@example.com", "short"),
        ("ada@example.com", "ada@example.com"),
    ],
)
async def test_invalid_registration_creates_nothing(
    test_database_url: str, email: str, password: str
) -> None:
    await _upgrade(test_database_url)
    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
            headers=ORIGIN,
        )
        assert response.status_code == 422
        assert password not in response.text
        async with app.state.session_factory() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.email.is_not(None))
                )
                == 0
            )
            assert await db.scalar(select(func.count()).select_from(AuthSession)) == 0
