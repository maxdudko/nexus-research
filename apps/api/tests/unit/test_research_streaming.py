"""Request-scoped research streams. No paid provider calls."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from langgraph.checkpoint.memory import InMemorySaver
from tests.conftest import PRINCIPAL, client_for, make_settings, signed_in
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
from nexus.api.deps import CurrentUser, get_current_user
from nexus.api.v1.research import get_runner
from nexus.api.v1.runs import get_history
from nexus.main import create_app
from nexus.research.coordinator import RunCoordinator
from nexus.research.history import HistoryConflict
from nexus.research.runner import ResearchRunner
from nexus.schemas.report import GraphUsage, ResearchReport
from nexus.schemas.research import ResearchPlan

pytestmark = pytest.mark.usefixtures("stub_scopes")


class MemoryHistory:
    """In-memory domain rows for stream tests."""

    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, object]] = {}
        self.order: list[str] = []
        self.fail_success = False
        self.block_failure = False
        self.delay_failure = 0.0
        self.fail_failure = False
        self.fail_create = False

    async def create_running(
        self,
        run_id: UUID,
        research_id: UUID,
        question: str,
        now: datetime,
        *,
        owner_id: UUID | None = None,
    ) -> None:
        del owner_id
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
        *,
        session_gate: object = None,
    ) -> bool:
        del warnings, usage, now, session_gate
        self.order.append("finalize_success")
        if self.fail_success:
            raise RuntimeError("commit failed")
        row = self.rows[run_id]
        if row["status"] != "running":
            raise HistoryConflict
        row["status"] = report.status
        row["report"] = report
        return True

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
        if self.fail_failure:
            raise RuntimeError("commit failed")
        if self.block_failure:
            await asyncio.Event().wait()
        if self.delay_failure:
            await asyncio.sleep(self.delay_failure)
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
        self.calls = 0

    async def create_plan(self, question: str) -> ResearchPlan:
        del question
        self.calls += 1
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
    app = signed_in(create_app(make_settings(**settings)))

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


async def test_unstored_commit_failure_emits_no_terminal_frame() -> None:
    runner, _recording = _runner()
    history = MemoryHistory()
    history.fail_success = True
    history.fail_failure = True
    coordinator, run_id, research_id = _coordinator(runner, history)
    await history.create_running(run_id, research_id, QUESTION, runner.clock())

    names = [name for name, _data in parse_sse(await _frames(coordinator))]

    assert "research.started" in names
    assert "research.completed" not in names
    assert "research.failed" not in names
    assert "research.interrupted" not in names
    assert history.rows[run_id]["status"] == "running"
    assert history.rows[run_id]["report"] is None


async def test_transport_cancel_after_commit_keeps_the_stored_report() -> None:
    runner, _recording = _runner()
    history = MemoryHistory()
    coordinator, run_id, research_id = _coordinator(runner, history)
    await history.create_running(run_id, research_id, QUESTION, runner.clock())
    frames = coordinator.events()
    seen: list[str] = []
    while True:
        frame = await anext(frames)
        seen.append(frame)
        if "research.completed" in frame:
            with pytest.raises(asyncio.CancelledError):
                await frames.athrow(asyncio.CancelledError())
            break

    assert any("research.completed" in frame for frame in seen)
    assert history.order.count("finalize_success") == 1
    assert "finalize_failure" not in history.order
    assert history.rows[run_id]["status"] == "insufficient_evidence"
    assert history.rows[run_id]["report"] is not None


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


class _HoldPlanner:
    """Blocks in the first plans until the test opens the gate."""

    def __init__(self, ready_at: int) -> None:
        self.ready_at = ready_at
        self.calls = 0
        self.ready = asyncio.Event()
        self.release = asyncio.Event()

    async def create_plan(self, question: str) -> ResearchPlan:
        del question
        self.calls += 1
        if self.calls >= self.ready_at:
            self.ready.set()
        await self.release.wait()
        return plan_for(["alpha limits"])


async def test_a_second_stream_for_one_user_does_not_take_the_semaphore() -> None:
    planner = _HoldPlanner(ready_at=1)
    runner, _recording = _runner(planner=planner, timeout_seconds=5)
    app = _install(runner, MemoryHistory(), stream_max_concurrent=4)

    async with client_for(app) as client:
        first = asyncio.create_task(
            client.post("/api/v1/research/stream", json={"question": QUESTION})
        )
        await planner.ready.wait()
        held = app.state.stream_semaphore._value
        second = await client.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
        )
        still_held = app.state.stream_semaphore._value
        planner.release.set()
        first_response = await first

    assert held == 3
    assert still_held == 3
    assert second.status_code == 429
    assert second.headers["retry-after"] == "1"
    assert second.json()["error"]["code"] == "too_many_requests"
    assert first_response.status_code == 200
    assert planner.calls == 1


async def test_two_users_can_each_hold_one_stream_under_the_process_cap() -> None:
    bea = CurrentUser(
        id=UUID("33333333-3333-4333-8333-333333333333"),
        email="bea@example.com",
        role="user",
        session_id=UUID("44444444-4444-4444-8444-444444444444"),
    )
    planner = _HoldPlanner(ready_at=2)
    runner, _recording = _runner(planner=planner, timeout_seconds=5)
    app = _install(runner, MemoryHistory(), stream_max_concurrent=4)

    async def current(request: Request) -> CurrentUser:
        if request.headers.get("x-test-user") == "bea":
            return bea
        return PRINCIPAL

    app.dependency_overrides[get_current_user] = current

    async with client_for(app) as client:
        first = asyncio.create_task(
            client.post("/api/v1/research/stream", json={"question": QUESTION})
        )
        second = asyncio.create_task(
            client.post(
                "/api/v1/research/stream",
                json={"question": QUESTION},
                headers={"x-test-user": "bea"},
            )
        )
        await planner.ready.wait()
        held = app.state.stream_semaphore._value
        third = await client.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
        )
        still_held = app.state.stream_semaphore._value
        planner.release.set()
        first_response = await first
        second_response = await second

    assert held == 2
    assert still_held == 2
    assert third.status_code == 429
    assert third.headers["retry-after"] == "1"
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert planner.calls == 2


async def test_the_process_cap_still_rejects_another_user() -> None:
    bea = CurrentUser(
        id=UUID("33333333-3333-4333-8333-333333333333"),
        email="bea@example.com",
        role="user",
        session_id=UUID("44444444-4444-4444-8444-444444444444"),
    )
    planner = BlockingPlanner()
    runner, _recording = _runner(planner=planner, timeout_seconds=5)
    app = _install(runner, MemoryHistory(), stream_max_concurrent=1)

    async def current(request: Request) -> CurrentUser:
        if request.headers.get("x-test-user") == "bea":
            return bea
        return PRINCIPAL

    app.dependency_overrides[get_current_user] = current

    async with client_for(app) as client:
        first = asyncio.create_task(
            client.post("/api/v1/research/stream", json={"question": QUESTION})
        )
        await planner.started.wait()
        second = await client.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
            headers={"x-test-user": "bea"},
        )
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

    assert second.status_code == 429
    assert second.json()["error"]["message"] == (
        "Too many research runs are in progress."
    )
    assert "retry-after" not in second.headers


async def test_logout_flag_interrupts_a_blocked_stream_on_the_heartbeat() -> None:
    planner = BlockingPlanner()
    runner, _recording = _runner(planner=planner, timeout_seconds=5)
    alive = {"ok": True}

    async def session_active() -> bool:
        return alive["ok"]

    runner.context.session_active = session_active
    history = MemoryHistory()
    app = _install(runner, history, stream_heartbeat_seconds=0.05)

    async with client_for(app) as client:
        task = asyncio.create_task(
            client.post("/api/v1/research/stream", json={"question": QUESTION})
        )
        await planner.started.wait()
        alive["ok"] = False
        response = await asyncio.wait_for(task, timeout=2)

    assert response.status_code == 200
    assert "research.interrupted" in response.text
    assert planner.calls == 1
    row = next(iter(history.rows.values()))
    assert row["status"] == "interrupted"
    assert row["error_code"] == "interrupted"


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


async def test_disconnect_waits_for_a_slow_status_write() -> None:
    planner = BlockingPlanner()
    runner, _recording = _runner(planner=planner, timeout_seconds=5)
    history = MemoryHistory()
    history.delay_failure = 0.3
    app = _install(runner, history, stream_cancel_persist_seconds=2)

    async with app.router.lifespan_context(app):
        started = asyncio.get_running_loop().time()
        await _disconnect_after_started(app)
        elapsed = asyncio.get_running_loop().time() - started

    row = next(iter(history.rows.values()))
    assert row["status"] == "interrupted"
    assert row["error_code"] == "interrupted"
    assert row["report"] is None
    assert elapsed >= 0.3


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
