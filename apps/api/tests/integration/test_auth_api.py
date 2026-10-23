"""Authenticated browser session against PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select, text, update
from tests.conftest import client_for, make_settings
from tests.integration.conftest import run_alembic

from nexus.identity.cli import run_cli
from nexus.identity.passwords import hash_password, needs_rehash, verify_password
from nexus.identity.store import (
    create_session,
    expires_at,
    get_session_by_hash,
    get_user_by_email,
    revoke_session,
)
from nexus.identity.tokens import csrf_token, new_token, token_hash
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.main import create_app
from nexus.models.user import User

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-1"
OTHER = "correct-horse-2"
PEPPER = "integration-test-pepper-0123456789abcd"
ORIGIN = {"origin": "http://127.0.0.1:3000"}
INVALID = {
    "error": {
        "code": "invalid_credentials",
        "message": "The email or password is incorrect.",
    }
}
UNAUTHENTICATED = {
    "error": {
        "code": "unauthenticated",
        "message": "Authentication is required.",
    }
}


def _settings(database_url: str, **overrides: object):
    values: dict[str, object] = {
        "environment": "test",
        "database_url": database_url,
        "session_pepper": PEPPER,
    }
    values.update(overrides)
    return make_settings(**values)


async def _cli(database_url: str, argv: list[str], stdin: str) -> int:
    return await asyncio.to_thread(
        run_cli,
        argv,
        stdin=StringIO(stdin),
        stderr=StringIO(),
        settings=_settings(database_url),
    )


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


def _cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


async def test_login_logout_and_identical_failures(test_database_url: str) -> None:
    await _upgrade(test_database_url)
    assert (
        await _cli(
            test_database_url, ["bootstrap-admin", "admin@example.com"], PASSWORD + "\n"
        )
        == 0
    )
    assert (
        await _cli(
            test_database_url, ["create-user", "ada@example.com"], PASSWORD + "\n"
        )
        == 0
    )
    assert (
        await _cli(
            test_database_url, ["create-user", "bea@example.com"], PASSWORD + "\n"
        )
        == 0
    )
    assert await _cli(test_database_url, ["disable-user", "bea@example.com"], "") == 0

    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        unknown = await client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": OTHER},
            headers=ORIGIN,
        )
        disabled = await client.post(
            "/api/v1/auth/login",
            json={"email": "bea@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert unknown.status_code == wrong.status_code == disabled.status_code == 401
        assert unknown.json() == wrong.json() == disabled.json() == INVALID
        assert unknown.headers["cache-control"] == "no-store"
        assert "set-cookie" not in unknown.headers

        logged_in = await client.post(
            "/api/v1/auth/login",
            json={"email": "Ada@Example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert logged_in.status_code == 200
        assert logged_in.headers["cache-control"] == "no-store"
        body = logged_in.json()
        assert body["email"] == "ada@example.com"
        assert body["role"] == "user"
        cookies = _cookie_headers(logged_in)
        session = next(item for item in cookies if item.startswith("nexus_session="))
        csrf = next(item for item in cookies if item.startswith("nexus_csrf="))
        assert "HttpOnly" in session
        assert "HttpOnly" not in csrf
        assert "Secure" not in session
        assert "SameSite=Lax" in session or "SameSite=lax" in session
        assert "Path=/" in session
        assert "Domain=" not in session
        assert "Max-Age=43200" in session

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.headers["cache-control"] == "no-store"
        assert me.json()["email"] == "ada@example.com"
        assert "password" not in me.text

        blocked = await client.post("/api/v1/auth/logout")
        assert blocked.status_code == 403
        assert (await client.get("/api/v1/auth/me")).status_code == 200

        token = client.cookies.get("nexus_csrf")
        logged_out = await client.post(
            "/api/v1/auth/logout",
            headers={**ORIGIN, "x-csrf-token": token or ""},
        )
        assert logged_out.status_code == 200
        assert logged_out.headers["cache-control"] == "no-store"
        gone = await client.get("/api/v1/auth/me")
        assert gone.status_code == 401
        assert gone.json() == UNAUTHENTICATED


async def test_secure_cookies_expired_revoked_and_rehash(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    assert (
        await _cli(
            test_database_url, ["create-user", "ada@example.com"], PASSWORD + "\n"
        )
        == 0
    )
    secure_app = create_app(_settings(test_database_url, cookie_secure=True))
    async with client_for(secure_app) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        session = next(
            item
            for item in _cookie_headers(response)
            if item.startswith("nexus_session=")
        )
        assert "Secure" in session

    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    try:
        account = await get_user_by_email(factory, "ada@example.com")
        assert account is not None
        weak = PasswordHasher(
            time_cost=1, memory_cost=8, parallelism=1, hash_len=16, salt_len=8
        )
        weak_hash = weak.hash(PASSWORD)
        assert needs_rehash(weak_hash) is True
        async with factory() as db, db.begin():
            await db.execute(
                update(User)
                .where(User.id == account.id)
                .values(password_hash=weak_hash)
            )
        app = create_app(_settings(test_database_url))
        async with client_for(app) as client:
            refreshed = await client.post(
                "/api/v1/auth/login",
                json={"email": "ada@example.com", "password": PASSWORD},
                headers=ORIGIN,
            )
            assert refreshed.status_code == 200
        updated = await get_user_by_email(factory, "ada@example.com")
        assert updated is not None
        assert updated.password_hash is not None
        assert needs_rehash(updated.password_hash) is False
        assert verify_password(PASSWORD, updated.password_hash) is True

        async with client_for(app) as client:
            signed_in = await client.post(
                "/api/v1/auth/login",
                json={"email": "ada@example.com", "password": PASSWORD},
                headers=ORIGIN,
            )
            assert signed_in.status_code == 200
            presented = client.cookies.get("nexus_session")
            assert presented is not None
            stored = await get_session_by_hash(factory, token_hash(presented, PEPPER))
            assert stored is not None
            await revoke_session(factory, stored.id, datetime.now(UTC))
            revoked = await client.get("/api/v1/auth/me")
            assert revoked.status_code == 401
            assert revoked.json() == UNAUTHENTICATED

        now = datetime.now(UTC)
        expired = new_token()
        await create_session(
            factory,
            account.id,
            token_hash(expired, PEPPER),
            now - timedelta(hours=13),
            now - timedelta(hours=1),
        )
        idle = new_token()
        await create_session(
            factory,
            account.id,
            token_hash(idle, PEPPER),
            now - timedelta(hours=5),
            expires_at(now - timedelta(hours=5), 12 * 60 * 60),
        )
        async with factory() as db, db.begin():
            row = await db.scalar(
                select(User.id).where(User.email == "ada@example.com")
            )
        assert row == account.id
        async with client_for(app) as client:
            old = await client.get(
                "/api/v1/auth/me",
                headers={"cookie": f"nexus_session={expired}"},
            )
            quiet = await client.get(
                "/api/v1/auth/me",
                headers={"cookie": f"nexus_session={idle}"},
            )
            assert old.status_code == 401
            assert quiet.status_code == 401
            assert old.json() == quiet.json() == UNAUTHENTICATED
    finally:
        await engine.dispose()


async def test_cookie_on_plan_requires_csrf_and_anonymous_plan_is_rejected(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    assert (
        await _cli(
            test_database_url, ["create-user", "ada@example.com"], PASSWORD + "\n"
        )
        == 0
    )
    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        blocked = await client.post(
            "/api/v1/research/plan",
            json={"question": "short"},
            headers=ORIGIN,
        )
        token = client.cookies.get("nexus_csrf")
        reached = await client.post(
            "/api/v1/research/plan",
            json={"question": "short"},
            headers={**ORIGIN, "x-csrf-token": token or ""},
        )
    async with client_for(app) as anonymous_client:
        anonymous = await anonymous_client.post(
            "/api/v1/research/plan",
            json={"question": "short"},
        )
    assert blocked.status_code == 403
    assert reached.status_code == 422
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "unauthenticated"


async def test_five_bad_logins_stop_before_another_argon2(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _upgrade(test_database_url)
    assert (
        await _cli(
            test_database_url,
            ["create-user", "limited@example.com"],
            PASSWORD + "\n",
        )
        == 0
    )
    calls = {"n": 0}
    real = verify_password

    def spy(password: str, hashed: str) -> bool:
        calls["n"] += 1
        return real(password, hashed)

    monkeypatch.setattr("nexus.api.v1.auth.verify_password", spy)
    app = create_app(_settings(test_database_url))
    async with client_for(app) as client:
        malformed = await client.post(
            "/api/v1/auth/login",
            json={"email": "limited@example.com"},
            headers=ORIGIN,
        )
        assert malformed.status_code == 422
        for _ in range(5):
            failed = await client.post(
                "/api/v1/auth/login",
                json={"email": "Limited@Example.com", "password": OTHER},
                headers=ORIGIN,
            )
            assert failed.status_code == 401
        blocked = await client.post(
            "/api/v1/auth/login",
            json={"email": "limited@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )

    assert calls["n"] == 5
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_requests"
    assert int(blocked.headers["retry-after"]) >= 1


async def test_the_address_bucket_blocks_before_argon2(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _upgrade(test_database_url)
    assert (
        await _cli(
            test_database_url, ["create-user", "ip-a@example.com"], PASSWORD + "\n"
        )
        == 0
    )
    assert (
        await _cli(test_database_url, ["create-user", "ip-b@example.com"], OTHER + "\n")
        == 0
    )
    calls = {"n": 0}
    real = verify_password

    def spy(password: str, hashed: str) -> bool:
        calls["n"] += 1
        return real(password, hashed)

    monkeypatch.setattr("nexus.api.v1.auth.verify_password", spy)
    app = create_app(_settings(test_database_url, login_max_ip_failures=2))
    clock = {"now": datetime.now(UTC)}
    app.state.limiter.clock = lambda: clock["now"]
    async with client_for(app) as client:
        first = await client.post(
            "/api/v1/auth/login",
            json={"email": "ip-a@example.com", "password": OTHER},
            headers=ORIGIN,
        )
        second = await client.post(
            "/api/v1/auth/login",
            json={"email": "ip-b@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        blocked = await client.post(
            "/api/v1/auth/login",
            json={"email": "ip-a@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert first.status_code == second.status_code == 401
        assert blocked.status_code == 429
        assert calls["n"] == 2
        clock["now"] += timedelta(seconds=15 * 60)
        allowed = await client.post(
            "/api/v1/auth/login",
            json={"email": "ip-a@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )

    assert allowed.status_code == 200
    assert calls["n"] == 3
    assert int(blocked.headers["retry-after"]) >= 1


def _cookie(client: object, name: str) -> str:
    value = client.cookies.get(name)  # type: ignore[attr-defined]
    assert isinstance(value, str) and value
    return value


async def test_csrf_rejects_swap_bad_signature_and_replay(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    assert (
        await _cli(
            test_database_url, ["create-user", "ada@example.com"], PASSWORD + "\n"
        )
        == 0
    )
    assert (
        await _cli(test_database_url, ["create-user", "bea@example.com"], OTHER + "\n")
        == 0
    )
    app = create_app(_settings(test_database_url))
    async with (
        client_for(app) as ada,
        client_for(app) as bea,
        client_for(app) as ada_again,
        client_for(app) as attacker,
    ):
        ada_login = await ada.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        bea_login = await bea.post(
            "/api/v1/auth/login",
            json={"email": "bea@example.com", "password": OTHER},
            headers=ORIGIN,
        )
        again = await ada_again.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert (
            ada_login.status_code == bea_login.status_code == again.status_code == 200
        )
        ada_session = _cookie(ada, "nexus_session")
        ada_csrf = _cookie(ada, "nexus_csrf")
        bea_csrf = _cookie(bea, "nexus_csrf")
        new_session = _cookie(ada_again, "nexus_session")
        assert ada_csrf == csrf_token(ada_session, PEPPER)
        assert ada_session != new_session
        forged = ada_csrf[:-1] + ("A" if ada_csrf[-1] != "A" else "B")
        swapped = await attacker.post(
            "/api/v1/auth/logout",
            headers={
                **ORIGIN,
                "x-csrf-token": bea_csrf,
                "cookie": f"nexus_session={ada_session}; nexus_csrf={bea_csrf}",
            },
        )
        invalid = await attacker.post(
            "/api/v1/auth/logout",
            headers={
                **ORIGIN,
                "x-csrf-token": forged,
                "cookie": f"nexus_session={ada_session}; nexus_csrf={forged}",
            },
        )
        replayed = await attacker.post(
            "/api/v1/auth/logout",
            headers={
                **ORIGIN,
                "x-csrf-token": ada_csrf,
                "cookie": f"nexus_session={new_session}; nexus_csrf={ada_csrf}",
            },
        )
        ada_me = await ada.get("/api/v1/auth/me")
        new_me = await ada_again.get("/api/v1/auth/me")
        bea_me = await bea.get("/api/v1/auth/me")

    assert swapped.status_code == invalid.status_code == replayed.status_code == 403
    assert ada_me.status_code == new_me.status_code == bea_me.status_code == 200


async def _wait_for_ungranted_lock(database_url: str) -> None:
    engine = create_db_engine(database_url)
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            async with engine.connect() as connection:
                waiting = await connection.scalar(
                    text("SELECT count(*) FROM pg_locks WHERE NOT granted")
                )
            if waiting:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("login did not wait on the account lock")
            await asyncio.sleep(0.02)
    finally:
        await engine.dispose()


async def test_login_does_not_issue_a_session_after_a_concurrent_reset(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    assert (
        await _cli(
            test_database_url, ["create-user", "ada@example.com"], PASSWORD + "\n"
        )
        == 0
    )
    app = create_app(_settings(test_database_url))
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    held = asyncio.Event()
    proceed = asyncio.Event()
    try:
        account = await get_user_by_email(factory, "ada@example.com")
        assert account is not None
        replacement = hash_password(OTHER)

        async def hold() -> None:
            async with factory() as db, db.begin():
                locked = await db.scalar(
                    select(User).where(User.id == account.id).with_for_update()
                )
                assert locked is not None
                held.set()
                await proceed.wait()
                locked.password_hash = replacement
                locked.updated_at = datetime.now(UTC)

        holder = asyncio.create_task(hold())
        try:
            await asyncio.wait_for(held.wait(), timeout=5)
            async with client_for(app) as client:
                login_task = asyncio.create_task(
                    client.post(
                        "/api/v1/auth/login",
                        json={"email": "ada@example.com", "password": PASSWORD},
                        headers=ORIGIN,
                        timeout=30,
                    )
                )
                await _wait_for_ungranted_lock(test_database_url)
                proceed.set()
                response = await asyncio.wait_for(login_task, timeout=30)
                me = await client.get("/api/v1/auth/me")
            await holder
        finally:
            proceed.set()
            await holder
    finally:
        await engine.dispose()

    assert response.status_code == 401
    assert response.json() == INVALID
    assert "set-cookie" not in response.headers
    assert me.status_code == 401
    assert me.json() == UNAUTHENTICATED
