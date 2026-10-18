"""Streaming runs persisted in PostgreSQL. The graph is a local fake."""

import uuid
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from tests.conftest import client_for, make_settings
from tests.integration.conftest import run_alembic
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

from nexus.api.v1.research import get_runner
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.main import create_app
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
    app = create_app(make_settings(database_url=test_database_url))

    async def override() -> ResearchRunner:
        return _runner()

    app.dependency_overrides[get_runner] = override
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
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
    app = create_app(make_settings(database_url=test_database_url))
    app.dependency_overrides[get_runner] = lambda: _runner(BlockingPlanner())
    engine = create_db_engine(test_database_url)
    history = ResearchHistory(session_factory(engine))
    try:
        async with app.router.lifespan_context(app):
            payload = await _disconnect_after_started(app)
        frames = parse_sse(payload.decode())
        started = next(data for name, data in frames if name == "research.started")
        assert started is not None
        run_id = uuid.UUID(str(started["run_id"]))
        stored = await history.get_run(run_id)
    finally:
        await engine.dispose()

    assert stored is not None
    assert stored.run.status == "interrupted"
    assert stored.report is None
    assert stored.run.error_code == "interrupted"
