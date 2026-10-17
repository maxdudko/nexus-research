"""Checkpoint setup and reconnects against the disposable local database."""

import asyncio

import pytest
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

from nexus.ai.errors import ResearchError
from nexus.research.checkpoint import (
    CheckpointRuntime,
    open_checkpointer,
    setup_checkpoint,
)
from nexus.research.graph import run_config
from nexus.research.nodes.synthesis import StaticSynthesizer
from nexus.research.runner import ResearchRunner

pytestmark = pytest.mark.integration

_SUCCESS = {"completed", "partial", "insufficient_evidence"}


class BlockingDiscovery:
    """Stops after planning so cancellation leaves the first checkpoint."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def discover(self, question: str):
        del question
        self.started.set()
        await asyncio.Event().wait()
        return empty_discovery()


def _context(
    *,
    discovery: object | None = None,
) -> object:
    return make_context(
        planner=FakePlanner(plan_for(["accepted digest"])),
        discovery=discovery or FakeDiscovery([empty_discovery()]),  # type: ignore[arg-type]
        ingestion=FakeIngestion(),
        retrieval=FakeRetrieval([]),
        evaluator=ScriptedEvaluator([coverage(sufficient=False)]),
        synthesizer=StaticSynthesizer(),
    )


async def test_checkpoint_setup_can_run_twice(test_database_url: str) -> None:
    await setup_checkpoint(test_database_url)
    await setup_checkpoint(test_database_url)


async def _latest_by_thread(runtime: CheckpointRuntime) -> dict[str, dict[str, object]]:
    best: dict[str, tuple[str, dict[str, object]]] = {}
    async for item in runtime.saver.alist(None):
        values = item.checkpoint.get("channel_values", {})
        if not isinstance(values, dict):
            continue
        thread = str(item.config["configurable"]["thread_id"])
        stamp = str(item.checkpoint.get("ts", ""))
        current = best.get(thread)
        if current is None or stamp >= current[0]:
            best[thread] = (stamp, values)
    return {thread: values for thread, (_stamp, values) in best.items()}


async def test_a_finished_run_is_readable_after_reconnect(
    test_database_url: str,
) -> None:
    await setup_checkpoint(test_database_url)
    runtime = await open_checkpointer(test_database_url)
    try:
        runner = ResearchRunner(graph=runtime.graph, context=_context())  # type: ignore[arg-type]
        result = await runner.run(QUESTION, None)
        run_id = str(result.run_id)
    finally:
        await runtime.pool.close()

    again = await open_checkpointer(test_database_url)
    try:
        before = await again.graph.aget_state(run_config(run_id))
        second = ResearchRunner(graph=again.graph, context=_context())  # type: ignore[arg-type]
        follow_up = await second.run(QUESTION, result.research_id)
        after = await again.graph.aget_state(run_config(run_id))
    finally:
        await again.pool.close()

    assert result.status == "insufficient_evidence"
    assert before.values["run_id"] == run_id
    assert before.values["status"] == "insufficient_evidence"
    assert before.values["report"] is not None
    assert follow_up.run_id != result.run_id
    assert follow_up.research_id == result.research_id
    assert after.values["run_id"] == run_id
    assert after.values["status"] == before.values["status"]
    assert after.values["report"] == before.values["report"]


async def test_cancellation_leaves_a_running_checkpoint(
    test_database_url: str,
) -> None:
    await setup_checkpoint(test_database_url)
    discovery = BlockingDiscovery()
    runtime = await open_checkpointer(test_database_url)
    try:
        runner = ResearchRunner(
            graph=runtime.graph,
            context=_context(discovery=discovery),  # type: ignore[arg-type]
        )
        started = asyncio.create_task(runner.run(QUESTION, None))
        await discovery.started.wait()
        started.cancel()
        with pytest.raises(asyncio.CancelledError):
            await started
        assert started.done()
    finally:
        await runtime.pool.close()

    again = await open_checkpointer(test_database_url)
    try:
        latest = await _latest_by_thread(again)
    finally:
        await again.pool.close()

    assert len(latest) == 1
    values = next(iter(latest.values()))
    assert values["status"] == "running"
    assert values["report"] is None
    assert values["status"] not in _SUCCESS


class SlowDiscovery:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def discover(self, question: str):
        del question
        self.started.set()
        await asyncio.sleep(5)
        return empty_discovery()


async def test_hard_timeout_stays_interrupted_after_reconnect(
    test_database_url: str,
) -> None:
    await setup_checkpoint(test_database_url)
    runtime = await open_checkpointer(test_database_url)
    try:
        runner = ResearchRunner(
            graph=runtime.graph,
            context=_context(discovery=SlowDiscovery()),  # type: ignore[arg-type]
            timeout_seconds=0.05,
        )
        with pytest.raises(ResearchError) as exc_info:
            await runner.run(QUESTION, None)
        latest = await _latest_by_thread(runtime)
    finally:
        await runtime.pool.close()

    assert exc_info.value.code == "deadline_exceeded"
    assert len(latest) == 1
    run_id = next(iter(latest))
    assert latest[run_id]["status"] == "interrupted"
    assert latest[run_id]["report"] is None

    again = await open_checkpointer(test_database_url)
    try:
        snapshot = await again.graph.aget_state(run_config(run_id))
    finally:
        await again.pool.close()

    assert snapshot.values["status"] == "interrupted"
    assert snapshot.values["report"] is None
    assert snapshot.values["status"] not in _SUCCESS
