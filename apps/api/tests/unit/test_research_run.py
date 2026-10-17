"""The run endpoint and runner stay on one request. No provider calls."""

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from tests.conftest import client_for, make_settings
from tests.unit.graph_support import (
    QUESTION,
    FakeDiscovery,
    FakeIngestion,
    FakePlanner,
    FakeRetrieval,
    ScriptedEvaluator,
    coverage,
    discovered,
    empty_discovery,
    make_context,
    make_match,
    plan_for,
)

from nexus.ai.errors import ResearchError, provider_not_configured
from nexus.ai.planner import Planner
from nexus.api.v1.research import get_runner
from nexus.main import create_app
from nexus.research.checkpoint import libpq_conninfo
from nexus.research.graph import build_graph, run_config
from nexus.research.nodes.synthesis import StaticSynthesizer
from nexus.research.runner import ResearchRunner
from nexus.schemas.research import ResearchPlan

SECRET = "sk-run-secret"


class BlockingPlanner:
    """Stops inside planning so a test can cancel the run."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def create_plan(self, question: str) -> ResearchPlan:
        del question
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("a cancelled planner continued")


class SlowDiscovery:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def discover(self, question: str):
        del question
        self.started.set()
        await asyncio.sleep(5)
        return empty_discovery()


def _runner(
    *,
    planner: object | None = None,
    discovery: object | None = None,
    retrieval: FakeRetrieval | None = None,
    evaluator: ScriptedEvaluator | None = None,
    timeout_seconds: float | None = None,
) -> tuple[ResearchRunner, object]:
    saver = InMemorySaver()
    graph = build_graph(saver)
    context = make_context(
        planner=planner or FakePlanner(plan_for(["alpha limits"])),  # type: ignore[arg-type]
        discovery=discovery or FakeDiscovery([empty_discovery()]),  # type: ignore[arg-type]
        ingestion=FakeIngestion(),
        retrieval=retrieval or FakeRetrieval([]),
        evaluator=evaluator or ScriptedEvaluator([coverage(sufficient=False)]),
        synthesizer=StaticSynthesizer(),
    )
    return (
        ResearchRunner(graph=graph, context=context, timeout_seconds=timeout_seconds),
        saver,
    )


def _install(runner: ResearchRunner):
    app = create_app(make_settings())

    async def override() -> ResearchRunner:
        return runner

    app.dependency_overrides[get_runner] = override
    return app


def test_conninfo_uses_libpq_without_the_asyncpg_driver() -> None:
    info = libpq_conninfo("postgresql+asyncpg://nexus:secret@127.0.0.1:5432/nexus")

    assert info.startswith("postgresql://")
    assert "+asyncpg" not in info


async def test_a_second_run_gets_a_new_thread() -> None:
    runner, _saver = _runner()

    first = await runner.run(QUESTION, None)
    second = await runner.run(QUESTION, first.research_id)
    snapshot = await runner.graph.aget_state(run_config(str(first.run_id)))

    assert first.run_id != second.run_id
    assert first.research_id == second.research_id
    assert snapshot.values["run_id"] == str(first.run_id)
    assert snapshot.values["status"] == first.status
    assert first.status == "insufficient_evidence"
    assert first.report.sources == []


async def test_provider_failure_is_not_a_completed_report() -> None:
    runner, _saver = _runner(planner=FakePlanner(provider_not_configured()))

    with pytest.raises(ResearchError) as exc_info:
        await runner.run(QUESTION, None)

    assert exc_info.value.code == "provider_not_configured"
    assert exc_info.value.status_code == 503


async def test_partial_report_cites_only_the_selected_passage() -> None:
    from uuid import uuid4

    runner, _saver = _runner(
        discovery=FakeDiscovery(
            [discovered("https://example.org/notes", search_calls=1, llm_calls=1)]
        ),
        retrieval=FakeRetrieval([make_match(uuid4())], adopt_scope=True),
        evaluator=ScriptedEvaluator([coverage(sufficient=False)]),
    )

    result = await runner.run(QUESTION, None)

    assert result.status == "partial"
    assert result.report.sources[0].evidence_id == "E1"
    assert result.report.sources[0].research_id == str(result.research_id)
    assert result.warnings


async def test_hard_timeout_does_not_return_a_report() -> None:
    discovery = SlowDiscovery()
    runner, saver = _runner(discovery=discovery, timeout_seconds=0.05)

    with pytest.raises(ResearchError) as exc_info:
        await runner.run(QUESTION, None)

    assert exc_info.value.code == "deadline_exceeded"
    assert exc_info.value.status_code == 504
    assert discovery.started.is_set()
    statuses = []
    async for item in saver.alist(None):
        values = item.checkpoint.get("channel_values", {})
        if isinstance(values, dict):
            statuses.append(values.get("status"))
    assert "completed" not in statuses
    assert "interrupted" in statuses


async def test_cancellation_does_not_leave_a_running_task() -> None:
    planner = BlockingPlanner()
    runner, _saver = _runner(planner=planner)
    baseline = {task for task in asyncio.all_tasks() if not task.done()}
    started = asyncio.create_task(runner.run(QUESTION, None))
    await planner.started.wait()

    started.cancel()
    with pytest.raises(asyncio.CancelledError):
        await started

    running = {task for task in asyncio.all_tasks() if not task.done()}
    assert started.done()
    assert running == baseline


async def test_invalid_run_request_names_the_field() -> None:
    app = _install(_runner()[0])

    async with client_for(app) as client:
        short = await client.post("/api/v1/research/run", json={"question": "short"})
        threaded = await client.post(
            "/api/v1/research/run",
            json={"question": QUESTION, "thread_id": "client-thread"},
        )

    assert short.status_code == 422
    assert short.json()["error"]["code"] == "invalid_request"
    assert "question" in short.json()["error"]["message"]
    assert threaded.status_code == 422
    assert "thread_id" in threaded.json()["error"]["message"]


async def test_missing_provider_key_uses_the_error_envelope() -> None:
    runner, _saver = _runner(planner=Planner(make_settings()))
    app = _install(runner)

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/run",
            json={"question": QUESTION, "research_id": None},
        )

    body = response.json()
    assert response.status_code == 503
    assert body["error"]["code"] == "provider_not_configured"
    assert "report" not in body
    assert SECRET not in response.text
