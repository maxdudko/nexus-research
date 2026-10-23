"""Identity schema, quarantine row, and operator account commands."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from tests.conftest import make_settings
from tests.integration.conftest import run_alembic

from nexus.identity.cli import run_cli
from nexus.identity.errors import IdentityError
from nexus.identity.store import (
    disable_user,
    get_user_by_email,
    issue_session,
    session_count,
    set_password,
)
from nexus.identity.tokens import token_hash
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.models.user import QUARANTINE_USER_ID, AuthSession, User

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-1"
OTHER_PASSWORD = "correct-horse-2"
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
PEPPER = "integration-test-pepper-0123456789abcd"


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


def _settings(database_url: str):
    return make_settings(
        environment="test",
        database_url=database_url,
        session_pepper=PEPPER,
    )


async def _cli(
    argv: list[str],
    *,
    stdin: StringIO,
    stderr: StringIO,
    settings: object,
) -> int:
    """The CLI starts its own loop, so it has to run off the test loop."""
    return await asyncio.to_thread(
        run_cli,
        argv,
        stdin=stdin,
        stderr=stderr,
        settings=settings,
    )


async def test_identity_upgrade_is_repeatable_and_downgrade_keeps_research(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    repeated = await run_alembic(test_database_url, "upgrade", "head")
    assert repeated.returncode == 0, repeated.stderr
    checked = await run_alembic(test_database_url, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr

    engine = create_db_engine(test_database_url)
    run_id = uuid.uuid4()
    research_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            quarantine = await connection.execute(
                text(
                    "SELECT email, role, status, password_hash "
                    "FROM users WHERE id = :id"
                ),
                {"id": QUARANTINE_USER_ID},
            )
            row = quarantine.one()
            assert row.email is None
            assert row.role == "user"
            assert row.status == "quarantine"
            assert row.password_hash is None
            await connection.execute(
                text(
                    "INSERT INTO research_scopes (research_id, owner_user_id) "
                    "VALUES (:research_id, :owner)"
                ),
                {"research_id": research_id, "owner": QUARANTINE_USER_ID},
            )
            await connection.execute(
                text(
                    "INSERT INTO research_runs (run_id, research_id, question, status) "
                    "VALUES (:run_id, :research_id, :question, 'running')"
                ),
                {
                    "run_id": run_id,
                    "research_id": research_id,
                    "question": "How does identity migration behave?",
                },
            )
    finally:
        await engine.dispose()

    downgraded = await run_alembic(test_database_url, "downgrade", "b7e2c91a4f10")
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            names = set(
                await connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
            surviving = await connection.scalar(
                text("SELECT question FROM research_runs WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
    finally:
        await engine.dispose()
    assert "users" not in names
    assert "auth_sessions" not in names
    assert "research_runs" in names
    assert "sources" in names
    assert surviving == "How does identity migration behave?"

    await _upgrade(test_database_url)
    checked_again = await run_alembic(test_database_url, "check")
    assert checked_again.returncode == 0, checked_again.stdout + checked_again.stderr


async def test_accounts_are_unique_and_active_rows_need_a_hash(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    try:
        code = await _cli(
            ["create-user", "Ada@Example.com"],
            stdin=StringIO(PASSWORD + "\n"),
            stderr=StringIO(),
            settings=_settings(test_database_url),
        )
        assert code == 0
        duplicate = StringIO()
        again = await _cli(
            ["create-user", "ada@example.com"],
            stdin=StringIO(OTHER_PASSWORD + "\n"),
            stderr=duplicate,
            settings=_settings(test_database_url),
        )
        assert again == 1
        assert "already exists" in duplicate.getvalue()
        assert PASSWORD not in duplicate.getvalue()
        account = await get_user_by_email(factory, "ada@example.com")
        assert account is not None
        assert account.email == "ada@example.com"
        with pytest.raises(IntegrityError):
            async with factory() as db, db.begin():
                db.add(
                    User(
                        id=uuid.uuid4(),
                        email="other@example.com",
                        role="user",
                        status="active",
                        password_hash=None,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
        with pytest.raises(IdentityError) as exc_info:
            await set_password(
                factory,
                QUARANTINE_USER_ID,
                OTHER_PASSWORD,
                NOW,
                audit=False,
            )
        assert exc_info.value.code == "quarantine"
    finally:
        await engine.dispose()


async def test_bootstrap_is_idempotent_and_disable_resets_revoke_sessions(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    settings = _settings(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    try:
        assert (
            await _cli(
                ["bootstrap-admin", "admin@example.com"],
                stdin=StringIO(PASSWORD + "\n"),
                stderr=StringIO(),
                settings=settings,
            )
            == 0
        )
        admin = await get_user_by_email(factory, "admin@example.com")
        assert admin is not None
        assert admin.role == "admin"
        first_hash = admin.password_hash
        notice = StringIO()
        assert (
            await _cli(
                ["bootstrap-admin", "other@example.com"],
                stdin=StringIO(OTHER_PASSWORD + "\n"),
                stderr=notice,
                settings=settings,
            )
            == 0
        )
        assert "administrator already provisioned" in notice.getvalue()
        again = await get_user_by_email(factory, "admin@example.com")
        assert again is not None
        assert again.password_hash == first_hash
        assert await get_user_by_email(factory, "other@example.com") is None

        digest = token_hash("session-token-value", PEPPER)
        async with factory() as db, db.begin():
            db.add(
                AuthSession(
                    id=uuid.uuid4(),
                    user_id=admin.id,
                    token_hash=digest,
                    created_at=NOW,
                    last_used_at=NOW,
                    expires_at=datetime(2026, 10, 23, 0, 0, tzinfo=UTC),
                    revoked_at=None,
                )
            )
        disabled = StringIO()
        assert (
            await _cli(
                ["disable-user", "admin@example.com"],
                stdin=StringIO(),
                stderr=disabled,
                settings=settings,
            )
            == 0
        )
        stored = await get_user_by_email(factory, "admin@example.com")
        assert stored is not None
        assert stored.status == "disabled"
        async with factory() as db, db.begin():
            revoked = await db.scalar(select(AuthSession.revoked_at))
        assert revoked is not None

        assert (
            await _cli(
                ["reset-password", "admin@example.com"],
                stdin=StringIO(OTHER_PASSWORD + "\n"),
                stderr=StringIO(),
                settings=settings,
            )
            == 0
        )
        reset = await get_user_by_email(factory, "admin@example.com")
        assert reset is not None
        assert reset.password_hash != first_hash
        assert reset.status == "disabled"
        assert PASSWORD not in (reset.password_hash or "")
    finally:
        await engine.dispose()


async def test_issue_session_rejects_a_stale_hash_and_a_disabled_account(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    settings = _settings(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    try:
        assert (
            await _cli(
                ["create-user", "ada@example.com"],
                stdin=StringIO(PASSWORD + "\n"),
                stderr=StringIO(),
                settings=settings,
            )
            == 0
        )
        account = await get_user_by_email(factory, "ada@example.com")
        assert account is not None
        assert account.password_hash is not None
        old_hash = account.password_hash
        issued = await issue_session(
            factory,
            account.id,
            old_hash,
            token_hash("token-one", PEPPER),
            NOW,
            NOW + timedelta(hours=12),
        )
        assert issued is not None
        await set_password(factory, account.id, OTHER_PASSWORD, NOW, audit=False)
        stale = await issue_session(
            factory,
            account.id,
            old_hash,
            token_hash("token-two", PEPPER),
            NOW,
            NOW + timedelta(hours=12),
        )
        assert stale is None
        assert await session_count(factory, account.id) == 1
        current = await get_user_by_email(factory, "ada@example.com")
        assert current is not None
        assert current.password_hash is not None
        await disable_user(factory, "ada@example.com", NOW)
        disabled = await issue_session(
            factory,
            account.id,
            current.password_hash,
            token_hash("token-three", PEPPER),
            NOW,
            NOW + timedelta(hours=12),
        )
        assert disabled is None
        assert await session_count(factory, account.id) == 1
    finally:
        await engine.dispose()
