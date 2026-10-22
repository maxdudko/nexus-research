"""Request-scoped research runs. A checkpoint is not a worker."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID, uuid4

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.ai.coverage import OpenAICoverageEvaluator
from nexus.ai.errors import (
    MODEL_REFUSED,
    ResearchError,
    checkpoint_unavailable,
    deadline_exceeded,
    invalid_model_output,
    invalid_state,
    provider_not_configured,
    provider_rate_limited,
    provider_timeout,
    provider_unavailable,
    run_interrupted,
    search_failed,
)
from nexus.ai.planner import Planner
from nexus.ai.synthesis import OpenAIReportSynthesizer
from nexus.core.config import Settings
from nexus.ingestion.service import IngestionService
from nexus.observability.context import bind_run
from nexus.observability.spans import operation
from nexus.observability.usage import emit_run_summary
from nexus.research.budgets import limits_from_settings
from nexus.research.discovery import Discovery
from nexus.research.graph import GraphContext, run_config
from nexus.research.state import (
    GraphStateError,
    ResearchState,
    graph_usage,
    initial_state,
    validate_state,
)
from nexus.retrieval.embeddings import OpenAIEmbedder
from nexus.retrieval.service import RetrievalService
from nexus.retrieval.vector_store import EvidenceVectorStore
from nexus.schemas.report import ResearchReport
from nexus.schemas.research import ResearchRunResponse

logger = logging.getLogger(__name__)

DEADLINE_GRACE_SECONDS = 5
_REPORT_STATUSES = {"completed", "partial", "insufficient_evidence"}


GraphRun = CompiledStateGraph[ResearchState, GraphContext, ResearchState, ResearchState]


@dataclass(frozen=True)
class GraphProgress:
    """A completed node. Counts come from the paired values snapshot."""

    node: str
    iteration: int
    pages_indexed: int
    search_calls: int


class ResearchRunner:
    """Run one new thread to a terminal state. The same thread is never reused."""

    def __init__(
        self,
        *,
        graph: GraphRun,
        context: GraphContext,
        timeout_seconds: float | None = None,
    ) -> None:
        self.graph = graph
        self._context = context
        self._timeout = timeout_seconds

    async def run(
        self,
        question: str,
        research_id: UUID | None,
    ) -> ResearchRunResponse:
        """Execute one question on a new thread.

        The checkpoint can be read after this call. ``ainvoke`` resumes a thread
        only when that same thread id is passed again. This method always mints
        a new id, so a stored ``running`` row is not started again.
        """
        scope = research_id or uuid4()
        run_id = uuid4()
        with bind_run(
            run_id=run_id,
            research_id=scope,
            stage="run",
            create_buffer=True,
        ):
            try:
                return await self._execute(scope, run_id, question)
            finally:
                emit_run_summary()

    async def stream(
        self,
        question: str,
        research_id: UUID,
        run_id: UUID,
    ) -> AsyncIterator[GraphProgress | ResearchRunResponse]:
        """Stream one caller-minted thread. This does not start a second run.

        Node updates are progress. The last item is the validated response from
        the same ``astream`` call, not a separate ``ainvoke``.
        """
        state = self._initial(research_id, run_id, question)
        with bind_run(
            run_id=run_id,
            research_id=research_id,
            stage="run",
            create_buffer=True,
        ):
            try:
                try:
                    async for item in self._stream_graph(state, run_id):
                        yield item
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._translate(exc, run_id)
            finally:
                emit_run_summary()

    async def _execute(
        self,
        scope: UUID,
        run_id: UUID,
        question: str,
    ) -> ResearchRunResponse:
        state = self._initial(scope, run_id, question)
        try:
            async with asyncio.timeout(self._budget()):
                with operation(operation="research_run", provider="nexus", stage="run"):
                    result = await self.graph.ainvoke(
                        state,
                        config=run_config(str(run_id)),
                        context=self._context,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._translate(exc, run_id)
        return _response(_checked(result))

    def clock(self) -> datetime:
        """The clock the graph and domain rows share for this runner."""
        return self._context.clock()

    def _initial(self, research_id: UUID, run_id: UUID, question: str) -> ResearchState:
        return initial_state(
            research_id=research_id,
            run_id=run_id,
            question=question,
            started_at=self.clock(),
        )

    async def _stream_graph(
        self,
        state: ResearchState,
        run_id: UUID,
    ) -> AsyncIterator[GraphProgress | ResearchRunResponse]:
        """Yield progress from one drive task so the deadline survives yields."""
        queue: asyncio.Queue[GraphProgress] = asyncio.Queue()
        task = asyncio.create_task(self._drive(state, run_id, queue))
        getter: asyncio.Task[GraphProgress] | None = None
        try:
            while True:
                if getter is None:
                    getter = asyncio.create_task(queue.get())
                done, _pending = await asyncio.wait(
                    {task, getter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if getter in done:
                    yield getter.result()
                    getter = None
                    continue
                break
            if getter is not None and not getter.done():
                getter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await getter
                getter = None
            while not queue.empty():
                yield queue.get_nowait()
            yield await task
        finally:
            if getter is not None and not getter.done():
                getter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await getter
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _drive(
        self,
        state: ResearchState,
        run_id: UUID,
        queue: asyncio.Queue[GraphProgress],
    ) -> ResearchRunResponse:
        latest: dict[str, object] | None = None
        pending: list[str] = []
        async with asyncio.timeout(self._budget()):
            with operation(operation="research_run", provider="nexus", stage="run"):
                stream = self.graph.astream(
                    state,
                    config=run_config(str(run_id)),
                    context=self._context,
                    stream_mode=["updates", "values"],
                )
                async for item in stream:
                    mode, payload = _stream_part(item)
                    if mode == "updates" and isinstance(payload, dict):
                        pending.extend(key for key in payload if isinstance(key, str))
                        continue
                    if mode == "values" and isinstance(payload, dict):
                        latest = payload
                        for node in pending:
                            await queue.put(_progress(node, payload))
                        pending.clear()
        if isinstance(latest, dict):
            for node in pending:
                await queue.put(_progress(node, latest))
        if not isinstance(latest, dict):
            raise invalid_state()
        return _response(_checked(latest))

    async def _translate(self, exc: Exception, run_id: UUID) -> NoReturn:
        """Map one graph failure. Cancellation is handled by the caller."""
        if isinstance(exc, TimeoutError):
            await self._interrupt(str(run_id))
            raise deadline_exceeded() from None
        if isinstance(exc, ResearchError):
            raise exc
        if _checkpoint_failure(exc):
            logger.warning("checkpoint write failed: %s", type(exc).__name__)
            raise checkpoint_unavailable() from None
        raise exc

    def _budget(self) -> float:
        if self._timeout is not None:
            return self._timeout
        return self._context.limits.deadline_seconds + DEADLINE_GRACE_SECONDS

    async def _interrupt(self, run_id: str) -> None:
        """Write the same terminal fields as ``mark_interrupted``.

        This is a status update for the hard HTTP timeout. It does not invoke
        the thread again. Cancellation does not call this method.
        """
        try:
            await self.graph.aupdate_state(
                run_config(run_id),
                {
                    "status": "interrupted",
                    "report": None,
                    "error_code": "interrupted",
                },
            )
        except Exception as exc:
            logger.warning(
                "could not record an interrupted run: %s", type(exc).__name__
            )


def production_context(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    store: EvidenceVectorStore,
    *,
    owner_user_id: UUID,
) -> GraphContext:
    """The services one HTTP run calls. The store is closed by the caller."""
    embedder = OpenAIEmbedder(settings)
    return GraphContext(
        planner=Planner(settings),
        discovery=Discovery(settings),
        ingestion=IngestionService(sessions, store, embedder, settings),
        retrieval=RetrievalService(sessions, store, embedder),
        evaluator=OpenAICoverageEvaluator(settings),
        synthesizer=OpenAIReportSynthesizer(settings),
        limits=limits_from_settings(settings),
        clock=lambda: datetime.now(UTC),
        owner_user_id=owner_user_id,
    )


def _stream_part(item: object) -> tuple[str, object]:
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
        return item[0], item[1]
    raise invalid_state()


def _progress(node: str, values: dict[str, object]) -> GraphProgress:
    usage = values.get("usage")
    pages = 0
    searches = 0
    if isinstance(usage, dict):
        pages = _count(usage.get("pages_indexed"))
        searches = _count(usage.get("search_calls"))
    return GraphProgress(
        node=node,
        iteration=_count(values.get("iteration")),
        pages_indexed=pages,
        search_calls=searches,
    )


def _count(value: object) -> int:
    if type(value) is int:
        return value
    return -1


def _checked(result: object) -> ResearchState:
    try:
        return validate_state(result)
    except GraphStateError:
        raise invalid_state() from None


def _response(state: ResearchState) -> ResearchRunResponse:
    status = state["status"]
    report = state["report"]
    if status in _REPORT_STATUSES and report is not None:
        parsed = ResearchReport.model_validate(report)
        if parsed.status != status:
            raise invalid_state()
        return ResearchRunResponse(
            research_id=UUID(state["research_id"]),
            run_id=UUID(state["run_id"]),
            status=parsed.status,
            report=parsed,
            warnings=list(state["warnings"]),
            usage=graph_usage(state),
        )
    if status == "interrupted":
        raise run_interrupted()
    if status == "running":
        raise invalid_state()
    raise _failure(state["error_code"])


def _failure(code: str | None) -> ResearchError:
    if code == "model_refused":
        return ResearchError("model_refused", 503, MODEL_REFUSED)
    builders = {
        "provider_not_configured": provider_not_configured,
        "provider_timeout": provider_timeout,
        "provider_rate_limited": provider_rate_limited,
        "provider_unavailable": provider_unavailable,
        "invalid_model_output": invalid_model_output,
        "invalid_state": invalid_state,
        "search_failed": search_failed,
        "checkpoint_unavailable": checkpoint_unavailable,
        "deadline_exceeded": deadline_exceeded,
        "interrupted": run_interrupted,
    }
    builder = builders.get(code or "")
    if builder is None:
        return provider_unavailable()
    return builder()


def _checkpoint_failure(exc: BaseException) -> bool:
    return type(exc).__module__.startswith("psycopg")
