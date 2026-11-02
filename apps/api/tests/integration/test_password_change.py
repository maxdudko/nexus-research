"""Self-service password changes and revocation races against PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, update
from tests.conftest import client_for
from tests.integration.test_auth_api import (
    ORIGIN,
    OTHER,
    PASSWORD,
    _settings,
    _upgrade,
    _wait_for_ungranted_lock,
)

from nexus.identity.errors import IdentityError
from nexus.identity.passwords import hash_password, verify_password
from nexus.identity.store import (
    change_password,
    get_user_by_email,
    issue_session,
    register_user,
)
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.main import create_app
from nexus.models.user import AuthSession, User

pytestmark = pytest.mark.integration


@pytest.fixture
async def account_store(test_database_url: str):
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    now = datetime.now(UTC)
    try:
        account = await register_user(
            factory,
            "ada@example.com",
            PASSWORD,
            b"a" * 32,
            now,
            now + timedelta(hours=1),
        )
        async with factory() as db:
            session_id = await db.scalar(
                select(AuthSession.id).where(AuthSession.user_id == account.id)
            )
        assert session_id is not None
        yield factory, account.id, session_id
    finally:
        await engine.dispose()


@pytest.mark.parametrize("role", ["user", "admin"])
async def test_change_password_revokes_every_session(
    test_database_url: str, role: str, caplog: pytest.LogCaptureFixture
) -> None:
    await _upgrade(test_database_url)
    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/register",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        assert response.status_code == 201
        user_id = UUID(response.json()["id"])
        old_cookies = dict(client.cookies)
        async with app.state.session_factory() as db, db.begin():
            await db.execute(update(User).where(User.id == user_id).values(role=role))
        assert (
            await client.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "ada@example.com", "password": PASSWORD},
            )
        ).status_code == 200
        changed = await client.post(
            "/api/v1/auth/change-password",
            headers={**ORIGIN, "x-csrf-token": client.cookies.get("nexus_csrf") or ""},
            json={
                "current_password": PASSWORD,
                "new_password": OTHER,
                "user_id": "ignored",
            },
        )
        assert changed.status_code == 200
        assert changed.json() == {"status": "ok"}
        assert changed.headers["cache-control"] == "no-store"
        assert len(changed.headers.get_list("set-cookie")) == 2
        assert all(
            "Max-Age=0" in value for value in changed.headers.get_list("set-cookie")
        )
        assert not client.cookies
        for cookies in [None, old_cookies]:
            headers = (
                {}
                if cookies is None
                else {
                    "cookie": "; ".join(
                        f"{key}={value}" for key, value in cookies.items()
                    )
                }
            )
            assert (
                await client.get("/api/v1/auth/me", headers=headers)
            ).status_code == 401
        async with app.state.session_factory() as db:
            user = await db.get(User, user_id)
            assert user is not None and verify_password(OTHER, user.password_hash or "")
            sessions = list(
                await db.scalars(
                    select(AuthSession).where(AuthSession.user_id == user_id)
                )
            )
            assert len(sessions) == 2 and all(
                s.revoked_at is not None for s in sessions
            )
        assert (
            await client.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "ada@example.com", "password": PASSWORD},
            )
        ).status_code == 401
        assert (
            await client.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "ada@example.com", "password": OTHER},
            )
        ).status_code == 200
    assert PASSWORD not in caplog.text and OTHER not in caplog.text


@pytest.mark.parametrize(
    "current,new,status,code",
    [
        ("incorrect-password", OTHER, 400, "current_password_incorrect"),
        (PASSWORD, "short", 422, "invalid_request"),
        (PASSWORD, "x" * 129, 422, "invalid_request"),
        (PASSWORD, "ada@example.com", 422, "invalid_request"),
        (PASSWORD, PASSWORD, 422, "invalid_request"),
    ],
)
async def test_failed_change_keeps_password_and_session(
    test_database_url: str, current: str, new: str, status: int, code: str
) -> None:
    await _upgrade(test_database_url)
    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        await client.post(
            "/api/v1/auth/register",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        changed = await client.post(
            "/api/v1/auth/change-password",
            headers={**ORIGIN, "x-csrf-token": client.cookies.get("nexus_csrf") or ""},
            json={"current_password": current, "new_password": new},
        )
        assert changed.status_code == status
        assert changed.json()["error"]["code"] == code
        assert current not in changed.text and new not in changed.text
        assert "set-cookie" not in changed.headers
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        account = await get_user_by_email(app.state.session_factory, "ada@example.com")
        assert account is not None and verify_password(
            PASSWORD, account.password_hash or ""
        )


async def test_password_change_rollback(
    account_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, user_id, session_id = account_store
    from nexus.identity import store

    original = store._revoke_all

    async def fail_after_revoke(db, target, now):
        await original(db, target, now)
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(store, "_revoke_all", fail_after_revoke)
    with pytest.raises(RuntimeError):
        await change_password(
            factory, user_id, session_id, PASSWORD, OTHER, idle_seconds=3600
        )
    account = await get_user_by_email(factory, "ada@example.com")
    assert account is not None and verify_password(
        PASSWORD, account.password_hash or ""
    )
    async with factory() as db:
        session = await db.get(AuthSession, session_id)
        assert session is not None and session.revoked_at is None


async def test_simultaneous_changes_only_one_succeeds(account_store) -> None:
    factory, user_id, session_id = account_store
    results = await asyncio.gather(
        *(
            change_password(
                factory, user_id, session_id, PASSWORD, OTHER, idle_seconds=3600
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    failures = [result for result in results if isinstance(result, IdentityError)]
    assert len(failures) == 1 and failures[0].code == "unauthenticated"


@pytest.mark.parametrize("operation", ["reset", "disable", "logout", "expire"])
async def test_waiting_change_rechecks_session(
    account_store, test_database_url: str, operation: str
) -> None:
    factory, user_id, session_id = account_store
    task = None
    try:
        async with factory() as db, db.begin():
            user = await db.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            session = await db.scalar(
                select(AuthSession)
                .where(AuthSession.id == session_id)
                .with_for_update()
            )
            assert user is not None and session is not None
            task = asyncio.create_task(
                change_password(
                    factory, user_id, session_id, PASSWORD, OTHER, idle_seconds=3600
                )
            )
            await _wait_for_ungranted_lock(test_database_url)
            if operation == "reset":
                user.password_hash = hash_password("operator-reset-password")
                session.revoked_at = datetime.now(UTC)
            elif operation == "disable":
                user.status = "disabled"
                session.revoked_at = datetime.now(UTC)
            elif operation == "logout":
                session.revoked_at = datetime.now(UTC)
            else:
                # Keep expires_at > created_at while making the session expired.
                session.created_at = datetime.now(UTC) - timedelta(hours=2)
                session.expires_at = datetime.now(UTC) - timedelta(hours=1)
        with pytest.raises(IdentityError, match="unauthenticated"):
            await asyncio.wait_for(task, timeout=10)
        account = await get_user_by_email(factory, "ada@example.com")
        assert account is not None and not verify_password(
            OTHER, account.password_hash or ""
        )
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_login_verified_before_change_cannot_issue_new_session(
    account_store, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, user_id, session_id = account_store
    from nexus.identity import store

    original = store._revoke_all
    held, release = asyncio.Event(), asyncio.Event()
    before = await get_user_by_email(factory, "ada@example.com")
    assert before is not None and before.password_hash is not None

    async def pause(db, target, now):
        await original(db, target, now)
        held.set()
        await release.wait()

    monkeypatch.setattr(store, "_revoke_all", pause)
    change = asyncio.create_task(
        change_password(
            factory, user_id, session_id, PASSWORD, OTHER, idle_seconds=3600
        )
    )
    login = None
    try:
        await asyncio.wait_for(held.wait(), timeout=5)
        now = datetime.now(UTC)
        login = asyncio.create_task(
            issue_session(
                factory,
                user_id,
                before.password_hash,
                b"b" * 32,
                now,
                now + timedelta(hours=1),
            )
        )
        await _wait_for_ungranted_lock(test_database_url)
        release.set()
        await asyncio.wait_for(change, timeout=10)
        assert await asyncio.wait_for(login, timeout=10) is None
    finally:
        release.set()
        await asyncio.gather(
            change, *([] if login is None else [login]), return_exceptions=True
        )
