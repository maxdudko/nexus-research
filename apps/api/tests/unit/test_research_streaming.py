"""Request-scoped research streams. No paid provider calls."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

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

from nexus.ai.errors import provider_not_configured
from nexus.api.v1.research import get_runner
from nexus.api.v1.runs import get_history
from nexus.main import create_app
from nexus.research.coordinator import RunCoordinator
from nexus.research.history import HistoryConflict
from nexus.research.runner import ResearchRunner
from nexus.schemas.report import GraphUsage, ResearchReport
from nexus.schemas.research import ResearchPlan


class MemoryHistory:
    """In-memory domain rows for stream tests."""

    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, object]] = {}
        self.order: list[str] = []
        self.fail_success = False
        self.block_failure = False
        self.fail_create = False

    async def create_running(
        self,
        run_id: UUID,
        research_id: UUID,
        question: str,
        now: datetime,
    ) -> None:
        if self.fail_create:
            self.fail_create = False
            raise RuntimeError("database unavailable")
        self.order.append("create")
        self.rows[run_id] = {
            "research_id": research_id,
            "question": question,
            "status": "running",
            "error_code": None,
            "report": None,
            "created_at": now,
        }

    async def finalize_success(
        self,
        run_id: UUID,
        report: ResearchReport,
        warnings: list[str],
        usage: GraphUsage,
        now: datetime,
    ) -> None:
        del warnings, usage, now
        self.order.append("finalize_success")
        if self.fail_success:
            raise RuntimeError("commit failed")
        row = self.rows[run_id]
        if row["status"] != "running":
            raise HistoryConflict
        row["status"] = report.status
        row["report"] = report

    async def finalize_failure(
        self,
        run_id: UUID,
        code: str,
        *,
        interrupted: bool,
        now: datetime,
    ) -> None:
        del now
        self.order.append("finalize_failure")
        if self.block_failure:
            await asyncio.Event().wait()
        row = self.rows[run_id]
        if row["status"] != "running":
            raise HistoryConflict
        row["status"] = "interrupted" if interrupted else "failed"
        row["error_code"] = code
        row["report"] = None


class RecordingGraph:
    """Count executions and keep the thread id from the stream call."""

    def __init__(self, graph: object) -> None:
        self._graph = graph
        self.astream_calls = 0
        self.ainvoke_calls = 0
        self.thread_ids: list[str] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._graph, name)

    def astream(
        self,
        state: object,
        config: object = None,
        **kwargs: object,
    ) -> object:
        self.astream_calls += 1
        if isinstance(config, dict):
            configurable = config.get("configurable")
            if isinstance(configurable, dict) and isinstance(
                configurable.get("thread_id"), str
            ):
                self.thread_ids.append(cast(str, configurable["thread_id"]))
        stream = self._graph.astream(state, config, **kwargs)  # type: ignore[attr-defined]
        return stream

    async def ainvoke(self, *args: object, **kwargs: object) -> object:
        self.ainvoke_calls += 1
        raise AssertionError("the stream started a second graph invocation")


class SlowPlanner:
    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def create_plan(self, question: str) -> ResearchPlan:
        del question
        await asyncio.sleep(self._delay)
        return plan_for(["alpha limits"])


class BlockingPlanner:
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


class BrokenGraph:
    def __init__(self) -> None:
        self.astream_calls = 0
        self.ainvoke_calls = 0

    def astream(self, *_args: object, **_kwargs: object) -> AsyncIterator[object]:
        self.astream_calls += 1
        return self._once()

    async def _once(self) -> AsyncIterator[object]:
        yield ("values", {"status": "running"})

    async def ainvoke(self, *_args: object, **_kwargs: object) -> object:
        self.ainvoke_calls += 1
        raise AssertionError("the stream started a second graph invocation")


def _runner(
    *,
    planner: object | None = None,
    discovery: object | None = None,
    retrieval: FakeRetrieval | None = None,
    timeout_seconds: float | None = None,
    record: bool = True,
) -> tuple[ResearchRunner, RecordingGraph | None]:
    graph = build_saved_graph()
    recording = RecordingGraph(graph) if record else None
    context = make_context(
        planner=planner or FakePlanner(plan_for(["alpha limits"])),  # type: ignore[arg-type]
        discovery=discovery or FakeDiscovery([empty_discovery()]),  # type: ignore[arg-type]
        ingestion=FakeIngestion(),
        retrieval=retrieval or FakeRetrieval([]),
        evaluator=ScriptedEvaluator([coverage(sufficient=False)]),
    )
    runner = ResearchRunner(
        graph=recording or graph,  # type: ignore[arg-type]
        context=context,
        timeout_seconds=timeout_seconds,
    )
    return runner, recording


def build_saved_graph() -> object:
    from nexus.research.graph import build_graph

    return build_graph(InMemorySaver())


def _coordinator(
    runner: ResearchRunner,
    history: MemoryHistory,
    *,
    heartbeat: float = 15,
    cancel_seconds: float = 2,
) -> tuple[RunCoordinator, UUID, UUID]:
    run_id = uuid4()
    research_id = uuid4()
    coordinator = RunCoordinator(
        runner=runner,
        history=history,
        question=QUESTION,
        research_id=research_id,
        run_id=run_id,
        heartbeat_seconds=heartbeat,
        cancel_persist_seconds=cancel_seconds,
        clock=runner.clock,
    )
    return coordinator, run_id, research_id


def _install(
    runner: ResearchRunner,
    history: MemoryHistory,
    **settings: object,
):
    app = create_app(make_settings(**settings))

    async def override_runner() -> ResearchRunner:
        return runner

    app.dependency_overrides[get_runner] = override_runner
    app.dependency_overrides[get_history] = lambda: history
    return app


def parse_sse(payload: str) -> list[tuple[str, dict[str, object] | None]]:
    frames: list[tuple[str, dict[str, object] | None]] = []
    for block in payload.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        if block.startswith(":"):
            frames.append(("ping", None))
            continue
        event_name = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if not event_name:
            continue
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise AssertionError(data)
        frames.append((event_name, parsed))
    return frames


async def _frames(coordinator: RunCoordinator) -> str:
    chunks = [frame async for frame in coordinator.events()]
    return "".join(chunks)


async def test_stream_emits_one_terminal_event_after_the_commit() -> None:
    runner, recording = _runner(
        discovery=FakeDiscovery(
            [
                discovered(
                    "https://example.org/secret-notes", search_calls=1, llm_calls=1
                )
            ]
        ),
        retrieval=FakeRetrieval(
            [make_match(uuid4(), text="secret excerpt")],
            adopt_scope=True,
        ),
    )
    history = MemoryHistory()
    coordinator, run_id, research_id = _coordinator(runner, history)
    await history.create_running(run_id, research_id, QUESTION, runner.clock())

    body = await _frames(coordinator)
    frames = parse_sse(body)
    names = [name for name, _data in frames]

    assert names[0] == "research.started"
    assert "research.progress" in names
    assert names[-1] == "research.completed"
    assert names.count("research.completed") == 1
    assert "research.failed" not in names
    assert history.order[-1] == "finalize_success"
    assert history.rows[run_id]["status"] == "partial"
    assert history.rows[run_id]["report"] is not None
    assert "secret-notes" not in body
    assert "secret excerpt" not in body
    assert recording is not None
    assert recording.astream_calls == 1
    assert recording.ainvoke_calls == 0
    assert recording.thread_ids == [str(run_id)]
    assert all(
        data is not None and data["run_id"] == str(run_id)
        for _name, data in frames
        if data is not None
    )


async def test_commit_failure_does_not_emit_completed() -> None:
    runner, _recording = _runner()
    history = MemoryHistory()
    history.fail_success = True
    coordinator, run_id, research_id = _coordinator(runner, history)
    await history.create_running(run_id, research_id, QUESTION, runner.clock())

    frames = parse_sse(await _frames(coordinator))
    names = [name for name, _data in frames]

    assert names[-1] == "research.failed"
    assert "research.completed" not in names
    failed = frames[-1][1]
    assert failed is not None
    assert failed["code"] == "history_unavailable"
    assert history.rows[run_id]["status"] == "failed"
    assert history.rows[run_id]["report"] is None


async def test_provider_failure_is_a_safe_terminal_code() -> None:
    runner, _recording = _runner(planner=FakePlanner(provider_not_configured()))
    history = MemoryHistory()
    coordinator, run_id, research_id = _coordinator(runner, history)
    await history.create_running(run_id, research_id, QUESTION, runner.clock())

    frames = parse_sse(await _frames(coordinator))
    failed = frames[-1][1]

    assert frames[-1][0] == "research.failed"
    assert failed is not None
    assert failed["code"] == "provider_not_configured"
    assert "secret" not in json.dumps(failed)
    assert history.rows[run_id]["status"] == "failed"
    assert history.rows[run_id]["report"] is None


async def test_deadline_interrupts_without_a_second_invocation() -> None:
    runner, recording = _runner(discovery=SlowDiscovery(), timeout_seconds=0.05)
    history = MemoryHistory()
    coordinator, run_id, research_id = _coordinator(runner, history)
    await history.create_running(run_id, research_id, QUESTION, runner.clock())

    frames = parse_sse(await _frames(coordinator))
    terminal = frames[-1]

    assert terminal[0] == "research.interrupted"
    assert terminal[1] is not None
    assert terminal[1]["code"] == "deadline_exceeded"
    assert history.rows[run_id]["status"] == "interrupted"
    assert history.rows[run_id]["report"] is None
    assert recording is not None
    assert recording.ainvoke_calls == 0


async def test_invalid_final_state_is_a_failure() -> None:
    context = make_context(
        planner=FakePlanner(plan_for(["alpha limits"])),
        discovery=FakeDiscovery([empty_discovery()]),
        ingestion=FakeIngestion(),
        retrieval=FakeRetrieval([]),
    )
    broken = BrokenGraph()
    runner = ResearchRunner(
        graph=broken,  # type: ignore[arg-type]
        context=context,
    )
    history = MemoryHistory()
    coordinator, run_id, research_id = _coordinator(runner, history)
    await history.create_running(run_id, research_id, QUESTION, runner.clock())

    frames = parse_sse(await _frames(coordinator))

    assert frames[-1][0] == "research.failed"
    assert frames[-1][1] is not None
    assert frames[-1][1]["code"] == "invalid_state"
    assert broken.astream_calls == 1
    assert broken.ainvoke_calls == 0
    assert history.rows[run_id]["report"] is None


async def test_slow_node_emits_a_heartbeat_before_progress() -> None:
    runner, _recording = _runner(planner=SlowPlanner(0.25), timeout_seconds=5)
    history = MemoryHistory()
    coordinator, run_id, research_id = _coordinator(runner, history, heartbeat=0.05)
    await history.create_running(run_id, research_id, QUESTION, runner.clock())

    body = await _frames(coordinator)

    assert body.index(": ping\n\n") < body.index("event: research.progress")
    assert parse_sse(body)[-1][0] == "research.completed"


async def test_http_stream_returns_sse_headers_and_one_run() -> None:
    runner, recording = _runner()
    history = MemoryHistory()
    app = _install(runner, history)

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    frames = parse_sse(response.text)
    assert [name for name, _data in frames][-1] == "research.completed"
    assert len(history.rows) == 1
    assert recording is not None
    assert recording.astream_calls == 1


async def test_short_question_stays_a_json_error() -> None:
    runner, _recording = _runner()
    app = _install(runner, MemoryHistory())

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/stream",
            json={"question": "short"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "text/event-stream" not in response.headers.get("content-type", "")


async def test_history_failure_before_headers_releases_the_permit() -> None:
    runner, _recording = _runner()
    history = MemoryHistory()
    history.fail_create = True
    app = _install(runner, history, stream_max_concurrent=1)

    async with client_for(app) as client:
        failed = await client.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
        )
        succeeded = await client.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
        )

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "history_unavailable"
    assert succeeded.status_code == 200


async def test_a_second_stream_is_rejected_while_one_is_running() -> None:
    planner = BlockingPlanner()
    runner, _recording = _runner(planner=planner, timeout_seconds=5)
    history = MemoryHistory()
    app = _install(runner, history, stream_max_concurrent=1)

    async with client_for(app) as client:
        first = asyncio.create_task(
            client.post("/api/v1/research/stream", json={"question": QUESTION})
        )
        await planner.started.wait()
        second = await client.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
        )
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

    assert second.status_code == 429
    assert second.json()["error"]["code"] == "too_many_requests"
    assert len(history.rows) == 1


async def test_asgi_disconnect_cancels_the_graph_and_marks_interrupted() -> None:
    planner = BlockingPlanner()
    runner, _recording = _runner(planner=planner, timeout_seconds=5)
    history = MemoryHistory()
    app = _install(runner, history)
    baseline = {task for task in asyncio.all_tasks() if not task.done()}

    async with app.router.lifespan_context(app):
        payload = await _disconnect_after_started(app)

    assert b"research.started" in payload
    assert len(history.rows) == 1
    row = next(iter(history.rows.values()))
    assert row["status"] == "interrupted"
    assert row["report"] is None
    running = baseline
    for _ in range(20):
        running = {task for task in asyncio.all_tasks() if not task.done()}
        if running == baseline:
            break
        await asyncio.sleep(0.05)
    assert running == baseline


async def test_disconnect_leaves_running_when_the_status_write_times_out() -> None:
    planner = BlockingPlanner()
    runner, _recording = _runner(planner=planner, timeout_seconds=5)
    history = MemoryHistory()
    history.block_failure = True
    app = _install(runner, history, stream_cancel_persist_seconds=0.05)

    async with app.router.lifespan_context(app):
        await _disconnect_after_started(app)

    row = next(iter(history.rows.values()))
    assert row["status"] == "running"
    assert row["report"] is None


async def _disconnect_after_started(app: object) -> bytes:
    """Drive the ASGI app until the started frame, then send http.disconnect."""
    incoming: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    await incoming.put(
        {
            "type": "http.request",
            "body": json.dumps({"question": QUESTION}).encode(),
            "more_body": False,
        }
    )
    chunks: list[bytes] = []
    started = asyncio.Event()

    async def receive() -> dict[str, object]:
        return await incoming.get()

    async def send(message: dict[str, object]) -> None:
        if message.get("type") != "http.response.body":
            return
        body = message.get("body", b"")
        if isinstance(body, bytes) and body:
            chunks.append(body)
        if b"research.started" in b"".join(chunks) and not started.is_set():
            started.set()
            await incoming.put({"type": "http.disconnect"})

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/research/stream",
        "raw_path": b"/api/v1/research/stream",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"host", b"test"),
        ],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
        "root_path": "",
    }
    task = asyncio.create_task(app(scope, receive, send))  # type: ignore[operator]
    await asyncio.wait_for(started.wait(), 3)
    try:
        await asyncio.wait_for(task, 3)
    except asyncio.CancelledError:
        pass
    return b"".join(chunks)
