"""Scope backfill, adoption, and downgrade against PostgreSQL."""

import asyncio
import uuid
from datetime import UTC, datetime
from io import StringIO

import pytest
from sqlalchemy import select, text, update
from tests.conftest import make_settings
from tests.integration.conftest import run_alembic

from nexus.identity.cli import run_cli
from nexus.identity.errors import IdentityError
from nexus.identity.scopes import adopt_scope
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.models.scope import ResearchScope
from nexus.models.user import QUARANTINE_USER_ID
from nexus.observability.logging import configure_logging

pytestmark = pytest.mark.integration

QUESTION = "How does a shared legacy scope survive the upgrade?"
HASH = "ab" * 32


async def _alembic(database_url: str, *args: str) -> None:
    completed = await run_alembic(database_url, *args)
    assert completed.returncode == 0, completed.stderr


async def _cli(database_url: str, argv: list[str], stdin: str) -> int:
    settings = make_settings(
        environment="test",
        database_url=database_url,
        session_pepper="integration-test-pepper-0123456789abcd",
    )
    return await asyncio.to_thread(
        run_cli,
        argv,
        stdin=StringIO(stdin),
        stderr=StringIO(),
        settings=settings,
    )


async def _counts(database_url: str) -> dict[str, int]:
    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as connection:
            runs = await connection.scalar(text("SELECT count(*) FROM research_runs"))
            reports = await connection.scalar(
                text("SELECT count(*) FROM research_reports")
            )
            sources = await connection.scalar(text("SELECT count(*) FROM sources"))
    finally:
        await engine.dispose()
    return {"runs": runs or 0, "reports": reports or 0, "sources": sources or 0}


async def test_fresh_upgrade_has_one_quarantine_user_and_no_scopes(
    test_database_url: str,
) -> None:
    await _alembic(test_database_url, "upgrade", "head")
    await _alembic(test_database_url, "upgrade", "head")
    checked = await run_alembic(test_database_url, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            users = await connection.scalar(text("SELECT count(*) FROM users"))
            scopes = await connection.scalar(
                text("SELECT count(*) FROM research_scopes")
            )
            quarantine = await connection.scalar(
                text("SELECT status FROM users WHERE id = :id"),
                {"id": QUARANTINE_USER_ID},
            )
            runs = await connection.scalar(text("SELECT count(*) FROM research_runs"))
            sources = await connection.scalar(text("SELECT count(*) FROM sources"))
    finally:
        await engine.dispose()
    assert users == 1
    assert quarantine == "quarantine"
    assert scopes == 0
    assert runs == 0
    assert sources == 0


async def test_legacy_ids_become_quarantine_scopes_without_rewriting_rows(
    test_database_url: str,
) -> None:
    await _alembic(test_database_url, "upgrade", "b7e2c91a4f10")
    shared = uuid.uuid4()
    run_only = uuid.uuid4()
    source_only = uuid.uuid4()
    shared_run = uuid.uuid4()
    engine = create_db_engine(test_database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO research_runs "
                    "(run_id, research_id, question, status) "
                    "VALUES (:run_id, :research_id, :question, 'running')"
                ),
                {"run_id": shared_run, "research_id": shared, "question": QUESTION},
            )
            await connection.execute(
                text(
                    "INSERT INTO research_runs "
                    "(run_id, research_id, question, status) "
                    "VALUES (:run_id, :research_id, :question, 'running')"
                ),
                {
                    "run_id": uuid.uuid4(),
                    "research_id": run_only,
                    "question": QUESTION,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO research_reports (run_id, report) "
                    "VALUES (:run_id, CAST(:report AS jsonb))"
                ),
                {"run_id": shared_run, "report": '{"title":"Legacy"}'},
            )
            for research_id, uri in (
                (shared, "https://example.org/shared"),
                (source_only, "https://example.org/source-only"),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO sources "
                        "(id, research_id, type, uri, status, chunk_count) "
                        "VALUES (:id, :research_id, 'WEB', :uri, 'PENDING', 0)"
                    ),
                    {"id": uuid.uuid4(), "research_id": research_id, "uri": uri},
                )
    finally:
        await engine.dispose()

    before = await _counts(test_database_url)
    await _alembic(test_database_url, "upgrade", "head")
    assert await _counts(test_database_url) == before

    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    try:
        async with factory() as db:
            scopes = list(await db.scalars(select(ResearchScope)))
    finally:
        await engine.dispose()
    assert {scope.research_id for scope in scopes} == {shared, run_only, source_only}
    assert {scope.owner_user_id for scope in scopes} == {QUARANTINE_USER_ID}


async def test_adopt_scope_is_explicit_and_downgrade_stops_for_a_real_owner(
    test_database_url: str,
) -> None:
    await _alembic(test_database_url, "upgrade", "head")
    research_id = uuid.uuid4()
    engine = create_db_engine(test_database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO research_scopes (research_id, owner_user_id) "
                    "VALUES (:research_id, :owner)"
                ),
                {"research_id": research_id, "owner": QUARANTINE_USER_ID},
            )
            await connection.execute(
                text(
                    "INSERT INTO research_runs "
                    "(run_id, research_id, question, status) "
                    "VALUES (:run_id, :research_id, :question, 'running')"
                ),
                {
                    "run_id": uuid.uuid4(),
                    "research_id": research_id,
                    "question": QUESTION,
                },
            )
    finally:
        await engine.dispose()

    settings = make_settings(
        environment="test",
        database_url=test_database_url,
        session_pepper="integration-test-pepper-0123456789abcd",
    )
    log = StringIO()
    configure_logging(settings, stream=log)
    created = await _cli(
        test_database_url,
        ["create-user", "ada@example.com"],
        "correct-horse-1\n",
    )
    assert created == 0
    adopted = await _cli(
        test_database_url,
        ["adopt-scope", str(research_id), "ada@example.com"],
        "",
    )
    assert adopted == 0
    text_log = log.getvalue()
    assert '"action":"adopt_scope"' in text_log
    assert QUESTION not in text_log

    refused = await _cli(
        test_database_url,
        ["adopt-scope", str(research_id), "ada@example.com"],
        "",
    )
    assert refused == 1

    held = uuid.uuid4()
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    try:
        async with factory() as db, db.begin():
            db.add(
                ResearchScope(
                    research_id=held,
                    owner_user_id=QUARANTINE_USER_ID,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        with pytest.raises(IdentityError) as exc_info:
            await adopt_scope(factory, held, QUARANTINE_USER_ID, datetime.now(UTC))
        assert exc_info.value.code == "quarantine"

        removed = await run_alembic(
            test_database_url,
            "downgrade",
            "e3b7d91c4a20",
        )
        assert removed.returncode == 0, removed.stderr
        blocked = await run_alembic(test_database_url, "downgrade", "-1")
        assert blocked.returncode != 0
        assert "real owner" in blocked.stderr
        async with engine.connect() as connection:
            still_there = await connection.scalar(
                text("SELECT count(*) FROM research_scopes")
            )
        assert still_there == 2
        async with factory() as db, db.begin():
            await db.execute(
                update(ResearchScope).values(owner_user_id=QUARANTINE_USER_ID)
            )
    finally:
        await engine.dispose()

    await _alembic(test_database_url, "downgrade", "-1")
    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            names = set(
                await connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
            surviving = await connection.scalar(
                text("SELECT question FROM research_runs")
            )
    finally:
        await engine.dispose()
    assert "research_scopes" not in names
    assert "research_runs" in names
    assert "sources" in names
    assert "users" in names
    assert surviving == QUESTION


async def test_identity_downgrade_refuses_while_scopes_exist(
    test_database_url: str,
) -> None:
    await _alembic(test_database_url, "upgrade", "head")
    research_id = uuid.uuid4()
    engine = create_db_engine(test_database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO research_scopes (research_id, owner_user_id) "
                    "VALUES (:research_id, :owner)"
                ),
                {"research_id": research_id, "owner": QUARANTINE_USER_ID},
            )
            await connection.execute(
                text(
                    "INSERT INTO research_runs "
                    "(run_id, research_id, question, status) "
                    "VALUES (:run_id, :research_id, :question, 'running')"
                ),
                {
                    "run_id": uuid.uuid4(),
                    "research_id": research_id,
                    "question": QUESTION,
                },
            )
    finally:
        await engine.dispose()

    stamped = await run_alembic(test_database_url, "stamp", "c8e4a1b27d05")
    assert stamped.returncode == 0, stamped.stderr
    blocked = await run_alembic(test_database_url, "downgrade", "-1")
    assert blocked.returncode != 0
    assert "research_scopes" in blocked.stderr + blocked.stdout
    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            names = set(
                await connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
            surviving = await connection.scalar(
                text("SELECT question FROM research_runs")
            )
    finally:
        await engine.dispose()
    assert version == "c8e4a1b27d05"
    assert {"users", "auth_sessions", "research_scopes", "research_runs"} <= names
    assert surviving == QUESTION
