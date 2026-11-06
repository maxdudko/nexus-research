"""Atomic email changes, session rotation, and authentication races."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from tests.conftest import client_for
from tests.integration.test_auth_api import (
    ORIGIN,
    OTHER,
    PASSWORD,
    _settings,
    _upgrade,
    _wait_for_ungranted_lock,
)
from tests.integration.test_password_change import account_store as account_store

from nexus.identity import store
from nexus.identity.errors import IdentityError
from nexus.identity.tokens import password_reset_hash
from nexus.main import create_app
from nexus.models.user import AuthSession, PasswordResetToken, User

pytestmark = pytest.mark.integration
NEW = "new@example.com"


async def change(factory, user_id, session_id, email=NEW, digest=b"b" * 32):
    return await store.change_email(
        factory,
        user_id,
        session_id,
        PASSWORD,
        email,
        digest,
        idle_seconds=3600,
        absolute_seconds=7200,
    )


@pytest.mark.parametrize("role", ["user", "admin"])
async def test_http_rotation_preserves_identity_and_ownership(
    test_database_url, monkeypatch, caplog, role
):
    await _upgrade(test_database_url)
    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        registered = await client.post(
            "/api/v1/auth/register",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        original = registered.json()
        original["role"] = role
        async with app.state.session_factory() as db, db.begin():
            await db.execute(
                update(User).where(User.id == UUID(original["id"])).values(role=role)
            )
        original_cookies = dict(client.cookies)
        csrf = {**ORIGIN, "x-csrf-token": client.cookies.get("nexus_csrf") or ""}
        research = await client.post("/api/v1/research/scopes", headers=csrf)
        await store.issue_password_reset(
            app.state.session_factory,
            "ada@example.com",
            password_reset_hash("old-reset", "pepper"),
        )
        # A second old session must also be revoked.
        await client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        second_cookies = dict(client.cookies)
        response = await client.post(
            "/api/v1/auth/change-email",
            headers={**ORIGIN, "x-csrf-token": client.cookies.get("nexus_csrf") or ""},
            json={
                "new_email": " New@Example.com ",
                "current_password": PASSWORD,
                "role": "admin",
            },
        )
        assert response.status_code == 200
        assert response.json() == original | {"email": NEW}
        assert response.headers["cache-control"] == "no-store"
        assert len(response.headers.get_list("set-cookie")) == 2
        assert dict(client.cookies) != second_cookies
        assert (await client.get("/api/v1/auth/me")).json() == response.json()
        for cookies in [original_cookies, second_cookies]:
            headers = {"cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
            assert (
                await client.get("/api/v1/auth/me", headers=headers)
            ).status_code == 401
        factory = app.state.session_factory
        assert await store.get_user_by_email(factory, "ada@example.com") is None
        account = await store.get_user_by_email(factory, NEW)
        assert str(account.id) == original["id"] and account.role == role
        async with factory() as db:
            sessions = list(await db.scalars(select(AuthSession)))
            assert (
                len(sessions) == 3 and sum(s.revoked_at is None for s in sessions) == 1
            )
            assert (await db.scalar(select(PasswordResetToken))).revoked_at is not None
            from nexus.models.scope import ResearchScope

            scope = await db.get(ResearchScope, UUID(research.json()["research_id"]))
            assert scope.owner_user_id == account.id
        # Recovery moves to the new address; old address no longer matches.
        assert (
            await store.issue_password_reset(factory, "ada@example.com", b"d" * 32)
            is None
        )
        assert await store.issue_password_reset(factory, NEW, b"e" * 32) == NEW
        old_login = await client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        new_login = await client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": NEW, "password": PASSWORD},
        )
        assert old_login.status_code == 401 and new_login.status_code == 200
    assert PASSWORD not in caplog.text and NEW not in caplog.text


@pytest.mark.parametrize(
    "email,password,status,code",
    [
        (NEW, "wrong-password", 400, "current_password_incorrect"),
        ("bad-email", PASSWORD, 422, "invalid_request"),
        (" ADA@example.com ", PASSWORD, 422, "invalid_request"),
        ("taken@example.com", PASSWORD, 409, "email_unavailable"),
    ],
)
async def test_invalid_change_preserves_session_and_tokens(
    test_database_url, email, password, status, code
):
    await _upgrade(test_database_url)
    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        await client.post(
            "/api/v1/auth/register",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        await store.create_user(
            app.state.session_factory,
            "taken@example.com",
            OTHER,
            role="user",
            now=datetime.now(UTC),
        )
        await store.disable_user(
            app.state.session_factory, "taken@example.com", datetime.now(UTC)
        )
        await store.issue_password_reset(
            app.state.session_factory, "ada@example.com", b"r" * 32
        )
        cookies = dict(client.cookies)
        response = await client.post(
            "/api/v1/auth/change-email",
            headers={**ORIGIN, "x-csrf-token": client.cookies.get("nexus_csrf") or ""},
            json={"new_email": email, "current_password": password},
        )
        assert (
            response.status_code == status and response.json()["error"]["code"] == code
        )
        assert password not in response.text and email not in response.text
        assert "set-cookie" not in response.headers and dict(client.cookies) == cookies
        assert (await client.get("/api/v1/auth/me")).json()[
            "email"
        ] == "ada@example.com"
        async with app.state.session_factory() as db:
            assert (await db.scalar(select(PasswordResetToken))).revoked_at is None


async def test_email_cannot_equal_password(account_store):
    factory, user_id, session_id = account_store
    await store.set_password(
        factory,
        user_id,
        "password@example.com",
        datetime.now(UTC),
        audit=False,
        revoke=False,
    )
    with pytest.raises(IdentityError, match="password_matches_email"):
        await store.change_email(
            factory,
            user_id,
            session_id,
            "password@example.com",
            "password@example.com",
            b"c" * 32,
            idle_seconds=3600,
            absolute_seconds=7200,
        )


async def test_transaction_rollback(account_store, monkeypatch):
    factory, user_id, session_id = account_store
    await store.issue_password_reset(factory, "ada@example.com", b"r" * 32)
    original = store._revoke_reset_tokens

    async def fail(db, target, now):
        await original(db, target, now)
        raise RuntimeError("rollback")

    monkeypatch.setattr(store, "_revoke_reset_tokens", fail)
    with pytest.raises(RuntimeError):
        await change(factory, user_id, session_id)
    assert (await store.get_user_by_email(factory, "ada@example.com")).id == user_id
    async with factory() as db:
        assert (await db.get(AuthSession, session_id)).revoked_at is None
        assert (await db.scalar(select(PasswordResetToken))).revoked_at is None
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 1


async def test_concurrent_changes_only_one_succeeds(account_store):
    factory, user_id, session_id = account_store
    results = await asyncio.gather(
        change(factory, user_id, session_id),
        change(factory, user_id, session_id, "another@example.com", b"c" * 32),
        return_exceptions=True,
    )
    assert sum(isinstance(r, store.Account) for r in results) == 1
    assert [r.code for r in results if isinstance(r, IdentityError)] == [
        "unauthenticated"
    ]


async def test_concurrent_duplicate_address(account_store):
    factory, user_id, session_id = account_store
    now = datetime.now(UTC)
    other = await store.register_user(
        factory, "other@example.com", PASSWORD, b"c" * 32, now, now + timedelta(hours=1)
    )
    async with factory() as db:
        other_session = await db.scalar(
            select(AuthSession.id).where(AuthSession.user_id == other.id)
        )
    results = await asyncio.gather(
        change(factory, user_id, session_id),
        change(factory, other.id, other_session, digest=b"d" * 32),
        return_exceptions=True,
    )
    assert sum(isinstance(r, store.Account) for r in results) == 1
    assert [r.code for r in results if isinstance(r, IdentityError)] == [
        "duplicate_email"
    ]


@pytest.mark.parametrize(
    "operation", ["password_change", "operator_reset", "disable", "recovery"]
)
async def test_waiting_email_change_cannot_use_revoked_session(
    account_store, test_database_url, monkeypatch, operation
):
    factory, user_id, session_id = account_store
    await store.issue_password_reset(factory, "ada@example.com", b"r" * 32)
    original = store._revoke_all
    held, release = asyncio.Event(), asyncio.Event()

    async def pause(db, target, now):
        await original(db, target, now)
        held.set()
        await release.wait()

    monkeypatch.setattr(store, "_revoke_all", pause)
    if operation == "password_change":
        mutation = store.change_password(
            factory, user_id, session_id, PASSWORD, OTHER, idle_seconds=3600
        )
    elif operation == "operator_reset":
        mutation = store.set_password(
            factory, user_id, OTHER, datetime.now(UTC), audit=True
        )
    elif operation == "disable":
        mutation = store.disable_user(factory, "ada@example.com", datetime.now(UTC))
    else:
        mutation = store.consume_password_reset(factory, b"r" * 32, OTHER)
    first = asyncio.create_task(mutation)
    waiting = None
    try:
        await asyncio.wait_for(held.wait(), timeout=5)
        waiting = asyncio.create_task(change(factory, user_id, session_id))
        await _wait_for_ungranted_lock(test_database_url)
        release.set()
        await asyncio.wait_for(first, timeout=10)
        with pytest.raises(IdentityError, match="unauthenticated"):
            await asyncio.wait_for(waiting, timeout=10)
    finally:
        release.set()
        await asyncio.gather(
            first, *([] if waiting is None else [waiting]), return_exceptions=True
        )


async def test_logout_wins_while_email_change_waits(account_store, test_database_url):
    factory, user_id, session_id = account_store
    async with factory() as db, db.begin():
        session = await db.scalar(
            select(AuthSession).where(AuthSession.id == session_id).with_for_update()
        )
        task = asyncio.create_task(change(factory, user_id, session_id))
        await _wait_for_ungranted_lock(test_database_url)
        session.revoked_at = datetime.now(UTC)
    with pytest.raises(IdentityError, match="unauthenticated"):
        await asyncio.wait_for(task, timeout=10)


async def test_old_email_login_waiting_for_change_cannot_issue_session(
    account_store, test_database_url, monkeypatch
):
    factory, user_id, session_id = account_store
    before = await store.get_user_by_email(factory, "ada@example.com")
    original = store._revoke_all
    held, release = asyncio.Event(), asyncio.Event()

    async def pause(db, target, now):
        await original(db, target, now)
        held.set()
        await release.wait()

    monkeypatch.setattr(store, "_revoke_all", pause)
    first = asyncio.create_task(change(factory, user_id, session_id))
    login = None
    try:
        await asyncio.wait_for(held.wait(), timeout=5)
        now = datetime.now(UTC)
        login = asyncio.create_task(
            store.issue_session(
                factory,
                user_id,
                before.password_hash,
                b"c" * 32,
                now,
                now + timedelta(hours=1),
                expected_email="ada@example.com",
            )
        )
        await _wait_for_ungranted_lock(test_database_url)
        release.set()
        await asyncio.wait_for(first, timeout=10)
        assert await asyncio.wait_for(login, timeout=10) is None
    finally:
        release.set()
        await asyncio.gather(
            first, *([] if login is None else [login]), return_exceptions=True
        )
