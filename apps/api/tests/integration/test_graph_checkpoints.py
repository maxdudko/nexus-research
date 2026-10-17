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

from nexus.research.checkpoint import open_checkpointer, setup_checkpoint
from nexus.research.graph import run_config
from nexus.research.nodes.synthesis import StaticSynthesizer
from nexus.research.runner import ResearchRunner

pytestmark = pytest.mark.integration


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
        snapshot = await again.graph.aget_state(run_config(run_id))
    finally:
        await again.pool.close()

    assert result.status == "insufficient_evidence"
    assert snapshot.values["run_id"] == run_id
    assert snapshot.values["status"] == "insufficient_evidence"
    assert snapshot.values["report"] is not None


async def test_cancellation_leaves_a_non_completed_checkpoint(
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
    finally:
        await runtime.pool.close()

    again = await open_checkpointer(test_database_url)
    try:
        statuses = []
        async for item in again.saver.alist(None):
            values = item.checkpoint.get("channel_values", {})
            if isinstance(values, dict):
                statuses.append(values.get("status"))
    finally:
        await again.pool.close()

    assert statuses
    assert "completed" not in statuses
