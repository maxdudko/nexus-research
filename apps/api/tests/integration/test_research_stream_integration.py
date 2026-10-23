"""Streaming runs persisted in PostgreSQL. The graph is a local fake."""

import asyncio
import uuid
from io import StringIO
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from tests.conftest import client_for, make_settings
from tests.integration.conftest import run_alembic
from tests.integration.scopes import quarantine_scopes
from tests.unit.graph_support import (
    QUESTION,
    FakeDiscovery,
    FakeIngestion,
    FakePlanner,
    FakeRetrieval,
    ScriptedEvaluator,
    coverage,
    empty_discovery,
    make_context,
    plan_for,
)
from tests.unit.test_research_streaming import (
    BlockingPlanner,
    _disconnect_after_started,
    parse_sse,
)

from nexus.api.deps import CurrentUser, get_current_user
from nexus.api.v1.research import get_runner
from nexus.identity.cli import run_cli
from nexus.identity.store import get_user_by_email
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.main import create_app
from nexus.models.user import QUARANTINE_USER_ID
from nexus.research.coordinator import RunCoordinator
from nexus.research.graph import build_graph
from nexus.research.history import ResearchHistory
from nexus.research.runner import ResearchRunner

pytestmark = pytest.mark.integration


def _runner(planner: object | None = None) -> ResearchRunner:
    context = make_context(
        planner=planner or FakePlanner(plan_for(["alpha limits"])),  # type: ignore[arg-type]
        discovery=FakeDiscovery([empty_discovery()]),
        ingestion=FakeIngestion(),
        retrieval=FakeRetrieval([]),
        evaluator=ScriptedEvaluator([coverage(sufficient=False)]),
    )
    return ResearchRunner(graph=build_graph(InMemorySaver()), context=context)


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


async def test_completed_stream_is_readable_after_the_terminal_event(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    settings = make_settings(database_url=test_database_url)
    created = await asyncio.to_thread(
        run_cli,
        ["create-user", "ada@example.com"],
        stdin=StringIO("correct-horse-1\n"),
        stderr=StringIO(),
        settings=settings,
    )
    assert created == 0
    app = create_app(settings)

    async def override() -> ResearchRunner:
        return _runner()

    app.dependency_overrides[get_runner] = override
    origin = {"origin": "http://127.0.0.1:3000"}
    async with client_for(app) as client:
        logged_in = await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": "correct-horse-1"},
            headers=origin,
        )
        assert logged_in.status_code == 200
        token = client.cookies.get("nexus_csrf")
        response = await client.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
            headers={**origin, "x-csrf-token": token or ""},
        )
        assert response.status_code == 200
        frames = parse_sse(response.text)
        assert frames[-1][0] == "research.completed"
        completed = frames[-1][1]
        assert completed is not None
        run_id = UUID(str(completed["run_id"]))
        detail = await client.get(f"/api/v1/research/runs/{run_id}")

    assert detail.status_code == 200
    body = detail.json()
    assert body["run"]["status"] == "insufficient_evidence"
    assert body["run"]["run_id"] == str(run_id)
    assert body["report"]["status"] == "insufficient_evidence"
    assert "secret" not in response.text


async def test_disconnect_persists_interrupted_without_a_report(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    settings = make_settings(database_url=test_database_url)
    created = await asyncio.to_thread(
        run_cli,
        ["create-user", "ada@example.com"],
        stdin=StringIO("correct-horse-1\n"),
        stderr=StringIO(),
        settings=settings,
    )
    assert created == 0
    app = create_app(settings)
    app.dependency_overrides[get_runner] = lambda: _runner(BlockingPlanner())
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    account = await get_user_by_email(factory, "ada@example.com")
    assert account is not None
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=account.id,
        email="ada@example.com",
        role="user",
        session_id=uuid.uuid4(),
    )
    history = ResearchHistory(factory)
    try:
        async with app.router.lifespan_context(app):
            payload = await _disconnect_after_started(app)
        frames = parse_sse(payload.decode())
        started = next(data for name, data in frames if name == "research.started")
        assert started is not None
        run_id = uuid.UUID(str(started["run_id"]))
        stored = await history.get_run(run_id, owner_id=account.id)
    finally:
        await engine.dispose()

    assert stored is not None
    assert stored.run.status == "interrupted"
    assert stored.report is None
    assert stored.run.error_code == "interrupted"


async def _install_trigger(database_url: str, *statements: str) -> None:
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


def _coordinator(
    runner: ResearchRunner,
    history: ResearchHistory,
) -> tuple[RunCoordinator, uuid.UUID, uuid.UUID]:
    run_id = uuid.uuid4()
    research_id = uuid.uuid4()
    coordinator = RunCoordinator(
        runner=runner,
        history=history,
        question=QUESTION,
        research_id=research_id,
        run_id=run_id,
        heartbeat_seconds=15,
        cancel_persist_seconds=2,
        clock=runner.clock,
    )
    return coordinator, run_id, research_id


async def test_report_insert_failure_stores_failed_without_completed(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    await _install_trigger(
        test_database_url,
        "CREATE OR REPLACE FUNCTION nexus_fail_report() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'report write failed'; END; $$",
        "CREATE TRIGGER nexus_fail_report BEFORE INSERT ON research_reports "
        "FOR EACH ROW EXECUTE FUNCTION nexus_fail_report()",
    )
    engine = create_db_engine(test_database_url)
    history = ResearchHistory(session_factory(engine))
    runner = _runner()
    coordinator, run_id, research_id = _coordinator(runner, history)
    await quarantine_scopes(session_factory(engine), research_id)
    try:
        await history.create_running(
            run_id,
            research_id,
            QUESTION,
            runner.clock(),
            owner_id=QUARANTINE_USER_ID,
        )
        body = "".join([frame async for frame in coordinator.events()])
        names = [name for name, _data in parse_sse(body)]
        stored = await history.get_run(run_id, owner_id=QUARANTINE_USER_ID)
    finally:
        await engine.dispose()

    assert "research.completed" not in names
    assert names[-1] == "research.failed"
    assert stored is not None
    assert stored.run.status == "failed"
    assert stored.run.error_code == "history_unavailable"
    assert stored.report is None


async def test_status_update_failure_leaves_running_without_a_terminal_frame(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    await _install_trigger(
        test_database_url,
        "CREATE OR REPLACE FUNCTION nexus_fail_status() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'status write failed'; END; $$",
        "CREATE TRIGGER nexus_fail_status BEFORE UPDATE ON research_runs "
        "FOR EACH ROW EXECUTE FUNCTION nexus_fail_status()",
    )
    engine = create_db_engine(test_database_url)
    history = ResearchHistory(session_factory(engine))
    runner = _runner()
    coordinator, run_id, research_id = _coordinator(runner, history)
    await quarantine_scopes(session_factory(engine), research_id)
    try:
        await history.create_running(
            run_id,
            research_id,
            QUESTION,
            runner.clock(),
            owner_id=QUARANTINE_USER_ID,
        )
        body = "".join([frame async for frame in coordinator.events()])
        names = [name for name, _data in parse_sse(body)]
        stored = await history.get_run(run_id, owner_id=QUARANTINE_USER_ID)
    finally:
        await engine.dispose()

    assert "research.started" in names
    assert "research.completed" not in names
    assert "research.failed" not in names
    assert "research.interrupted" not in names
    assert stored is not None
    assert stored.run.status == "running"
    assert stored.report is None
