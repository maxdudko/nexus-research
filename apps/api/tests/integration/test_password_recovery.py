"""Recovery migration, transport workflow, token lifecycle, and races."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from tests.conftest import client_for
from tests.integration.conftest import run_alembic
from tests.integration.test_auth_api import ORIGIN, OTHER, PASSWORD, _settings, _upgrade
from tests.integration.test_password_change import account_store as account_store

from nexus.identity.errors import IdentityError
from nexus.identity.passwords import verify_password
from nexus.identity.store import (
    change_password,
    consume_password_reset,
    disable_user,
    get_user_by_email,
    issue_password_reset,
    set_password,
)
from nexus.identity.tokens import password_reset_hash
from nexus.main import create_app
from nexus.models.user import AuthSession, PasswordResetToken


def digest(token="reset-token"):
    return password_reset_hash(token, "pepper")


pytestmark = pytest.mark.integration


async def test_recovery_upgrade_repeatable_and_matches_models(test_database_url):
    await _upgrade(test_database_url)
    for args in [
        ("upgrade", "head"),
        ("check",),
        ("downgrade", "f6c1a8d04e27"),
        ("upgrade", "head"),
        ("check",),
    ]:
        result = await run_alembic(test_database_url, *args)
        assert result.returncode == 0, result.stdout + result.stderr


async def test_email_request_and_http_reset(test_database_url, monkeypatch, caplog):
    await _upgrade(test_database_url)
    settings = _settings(
        test_database_url,
        smtp_host="smtp.example.com",
        smtp_sender="nexus@example.com",
        password_reset_origin="https://nexus.example.com",
    )
    app = create_app(settings)
    sent = []

    def send(settings, recipient, token):
        sent.append((recipient, token))

    monkeypatch.setattr("nexus.identity.mail.send_reset_email", send)
    async with client_for(app) as client:
        await client.post(
            "/api/v1/auth/register",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        old_cookies = dict(client.cookies)
        responses = []
        for email in [" Ada@Example.com ", "missing@example.com"]:
            responses.append(
                await client.post(
                    "/api/v1/auth/forgot-password",
                    headers={**ORIGIN, "cookie": "nexus_session=stale"},
                    json={"email": email},
                )
            )
        assert [r.status_code for r in responses] == [202, 202]
        assert responses[0].json() == responses[1].json()
        assert all(r.headers["cache-control"] == "no-store" for r in responses)
        assert len(sent) == 1 and sent[0][0] == "ada@example.com"
        token = sent[0][1]
        async with app.state.session_factory() as db:
            row = await db.scalar(select(PasswordResetToken))
            assert row is not None
            assert row.token_hash == password_reset_hash(
                token, settings.session_pepper.get_secret_value()
            )
            assert row.expires_at - row.created_at == timedelta(minutes=30)
            assert row.consumed_at is None
        # Merely requesting a link leaves the existing browser session usable.
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        reset = await client.post(
            "/api/v1/auth/reset-password",
            headers=ORIGIN,
            json={"token": token, "new_password": OTHER},
        )
        assert reset.status_code == 200 and reset.json() == {"status": "ok"}
        assert len(reset.headers.get_list("set-cookie")) == 2
        assert not client.cookies
        assert (
            await client.get(
                "/api/v1/auth/me",
                headers={
                    "cookie": "; ".join(f"{k}={v}" for k, v in old_cookies.items())
                },
            )
        ).status_code == 401
        replay = await client.post(
            "/api/v1/auth/reset-password",
            headers=ORIGIN,
            json={"token": token, "new_password": "another-password"},
        )
        assert (
            replay.status_code == 400
            and replay.json()["error"]["code"] == "invalid_reset_token"
        )
        for password, status in [(PASSWORD, 401), (OTHER, 200)]:
            assert (
                await client.post(
                    "/api/v1/auth/login",
                    headers=ORIGIN,
                    json={"email": "ada@example.com", "password": password},
                )
            ).status_code == status
    assert (
        token not in caplog.text
        and PASSWORD not in caplog.text
        and OTHER not in caplog.text
    )


async def test_disabled_request_is_uniform_and_sends_nothing(
    test_database_url, monkeypatch
):
    await _upgrade(test_database_url)
    app = create_app(
        _settings(
            test_database_url,
            smtp_host="smtp.example.com",
            smtp_sender="nexus@example.com",
            password_reset_origin="https://nexus.example.com",
        )
    )
    sent = []
    monkeypatch.setattr(
        "nexus.identity.mail.send_reset_email", lambda *args: sent.append(args)
    )
    async with client_for(app) as client:
        await client.post(
            "/api/v1/auth/register",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        await disable_user(
            app.state.session_factory, "ada@example.com", datetime.now(UTC)
        )
        disabled = await client.post(
            "/api/v1/auth/forgot-password",
            headers=ORIGIN,
            json={"email": "ada@example.com"},
        )
        unknown = await client.post(
            "/api/v1/auth/forgot-password",
            headers=ORIGIN,
            json={"email": "missing@example.com"},
        )
        assert disabled.status_code == unknown.status_code == 202
        assert disabled.json() == unknown.json()
    assert not sent


async def test_replacement_and_expiry(account_store):
    factory, user_id, session_id = account_store
    await issue_password_reset(factory, "ada@example.com", digest("first"))
    await issue_password_reset(factory, "ada@example.com", digest("second"))
    with pytest.raises(IdentityError, match="invalid_reset_token"):
        await consume_password_reset(factory, digest("first"), OTHER)
    async with factory() as db, db.begin():
        row = await db.scalar(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == digest("second")
            )
        )
        row.created_at = datetime.now(UTC) - timedelta(hours=2)
        row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    with pytest.raises(IdentityError, match="invalid_reset_token"):
        await consume_password_reset(factory, digest("second"), OTHER)
    async with factory() as db:
        assert (await db.get(AuthSession, session_id)).revoked_at is None


@pytest.mark.parametrize("operation", ["change", "operator_reset", "disable"])
async def test_other_mutations_invalidate_tokens(account_store, operation):
    factory, user_id, session_id = account_store
    await issue_password_reset(factory, "ada@example.com", digest())
    if operation == "change":
        await change_password(
            factory, user_id, session_id, PASSWORD, OTHER, idle_seconds=3600
        )
    elif operation == "operator_reset":
        await set_password(factory, user_id, OTHER, datetime.now(UTC), audit=True)
    else:
        await disable_user(factory, "ada@example.com", datetime.now(UTC))
    with pytest.raises(IdentityError, match="invalid_reset_token"):
        await consume_password_reset(factory, digest(), "attacker-password")


async def test_concurrent_consumption_only_one_succeeds(account_store):
    factory, user_id, session_id = account_store
    await issue_password_reset(factory, "ada@example.com", digest())
    results = await asyncio.gather(
        *(consume_password_reset(factory, digest(), OTHER) for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    failures = [r for r in results if isinstance(r, IdentityError)]
    assert len(failures) == 1 and failures[0].code == "invalid_reset_token"


async def test_concurrent_issuance_keeps_only_one_link(account_store):
    factory, user_id, session_id = account_store
    await asyncio.gather(
        *(
            issue_password_reset(factory, "ada@example.com", digest(str(i)))
            for i in range(3)
        )
    )
    async with factory() as db:
        rows = list(
            await db.scalars(
                select(PasswordResetToken).where(
                    PasswordResetToken.revoked_at.is_(None)
                )
            )
        )
        assert len(rows) == 1


@pytest.mark.parametrize("password", [PASSWORD, "ada@example.com", "short"])
async def test_invalid_password_does_not_consume_token(account_store, password):
    factory, user_id, session_id = account_store
    await issue_password_reset(factory, "ada@example.com", digest())
    with pytest.raises(IdentityError):
        await consume_password_reset(factory, digest(), password)
    await consume_password_reset(factory, digest(), OTHER)


async def test_reset_transaction_rolls_back(account_store, monkeypatch):
    from nexus.identity import store

    factory, user_id, session_id = account_store
    await issue_password_reset(factory, "ada@example.com", digest())
    original = store._revoke_reset_tokens

    async def fail(db, target, now):
        await original(db, target, now)
        raise RuntimeError("rollback")

    monkeypatch.setattr(store, "_revoke_reset_tokens", fail)
    with pytest.raises(RuntimeError):
        await consume_password_reset(factory, digest(), OTHER)
    account = await get_user_by_email(factory, "ada@example.com")
    assert verify_password(PASSWORD, account.password_hash)
    async with factory() as db:
        row = await db.scalar(select(PasswordResetToken))
        assert row.consumed_at is None and row.revoked_at is None
        assert (await db.get(AuthSession, session_id)).revoked_at is None
