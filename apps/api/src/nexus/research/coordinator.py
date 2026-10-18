"""Domain lifecycle around one streaming research run."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from nexus.ai.errors import ResearchError, invalid_state
from nexus.research.events import (
    ResearchCompleted,
    ResearchFailed,
    ResearchInterrupted,
    ResearchStarted,
)
from nexus.research.history import HistoryConflict
from nexus.research.runner import GraphProgress, ResearchRunner
from nexus.research.streaming import (
    HEARTBEAT,
    Heartbeat,
    SseWriter,
    progress_event,
    with_heartbeat,
)
from nexus.schemas.report import GraphUsage, ResearchReport
from nexus.schemas.research import ResearchRunResponse

logger = logging.getLogger(__name__)

_INTERRUPT_CODES = frozenset({"interrupted", "deadline_exceeded"})


class StreamHistory(Protocol):
    """The history calls a stream makes after the running row exists."""

    async def finalize_success(
        self,
        run_id: UUID,
        report: ResearchReport,
        warnings: list[str],
        usage: GraphUsage,
        now: datetime,
    ) -> None: ...

    async def finalize_failure(
        self,
        run_id: UUID,
        code: str,
        *,
        interrupted: bool,
        now: datetime,
    ) -> None: ...


class RunCoordinator:
    """Own one stream from started through a single terminal frame.

    The caller inserts the running row and mints ``run_id`` before the first
    frame. This object does not start a second graph invocation.
    """

    def __init__(
        self,
        *,
        runner: ResearchRunner,
        history: StreamHistory,
        question: str,
        research_id: UUID,
        run_id: UUID,
        heartbeat_seconds: float,
        cancel_persist_seconds: float,
        clock: Callable[[], datetime],
    ) -> None:
        self._runner = runner
        self._history = history
        self._question = question
        self._research_id = research_id
        self._run_id = run_id
        self._heartbeat = heartbeat_seconds
        self._cancel_seconds = cancel_persist_seconds
        self._clock = clock
        self._committed = False

    async def events(self) -> AsyncIterator[str]:
        """Yield SSE frames. Completion is yielded only after the report commit."""
        writer = SseWriter()
        try:
            yield writer.event(
                "research.started",
                ResearchStarted(run_id=self._run_id, research_id=self._research_id),
                terminal=False,
            )
            final: ResearchRunResponse | None = None
            async for item in with_heartbeat(
                self._runner.stream(self._question, self._research_id, self._run_id),
                self._heartbeat,
            ):
                if item is HEARTBEAT or isinstance(item, Heartbeat):
                    yield writer.ping()
                    continue
                if isinstance(item, GraphProgress):
                    event = progress_event(item, self._run_id)
                    if event is not None:
                        yield writer.event("research.progress", event, terminal=False)
                    continue
                if isinstance(item, ResearchRunResponse):
                    final = item
            if final is None or final.run_id != self._run_id:
                raise invalid_state()
            await self._history.finalize_success(
                self._run_id,
                final.report,
                list(final.warnings),
                final.usage,
                self._clock(),
            )
            self._committed = True
            yield writer.event(
                "research.completed",
                ResearchCompleted(
                    run_id=self._run_id,
                    research_id=self._research_id,
                    status=final.status,
                ),
                terminal=True,
            )
        except asyncio.CancelledError:
            if not self._committed:
                await self._best_effort_interrupt()
            raise
        except ResearchError as exc:
            if writer.terminal or self._committed:
                raise
            await self._persist_error(exc)
            yield _terminal_frame(writer, self._run_id, exc)
        except Exception as exc:
            if writer.terminal or self._committed:
                raise
            logger.warning("research stream failed: %s", type(exc).__name__)
            await self._persist_code("history_unavailable", interrupted=False)
            yield writer.event(
                "research.failed",
                ResearchFailed(run_id=self._run_id, code="history_unavailable"),
                terminal=True,
            )

    async def _persist_error(self, exc: ResearchError) -> None:
        interrupted = exc.code in _INTERRUPT_CODES
        code = exc.code if interrupted else _safe_failure_code(exc.code)
        await self._persist_code(code, interrupted=interrupted)

    async def _persist_code(self, code: str, *, interrupted: bool) -> None:
        try:
            await self._history.finalize_failure(
                self._run_id,
                code,
                interrupted=interrupted,
                now=self._clock(),
            )
        except HistoryConflict:
            return
        except Exception as exc:
            logger.warning(
                "could not store a terminal research run: %s",
                type(exc).__name__,
            )

    async def _best_effort_interrupt(self) -> None:
        """Persist interrupted without shielding the graph task."""
        task = asyncio.create_task(self._persist_code("interrupted", interrupted=True))
        try:
            await asyncio.wait_for(asyncio.shield(task), self._cancel_seconds)
        except TimeoutError:
            task.cancel()
            await _quiet(task)
            logger.warning("interrupted research run was not stored")
        except Exception as exc:
            logger.warning(
                "could not record an interrupted run: %s",
                type(exc).__name__,
            )


def _terminal_frame(writer: SseWriter, run_id: UUID, exc: ResearchError) -> str:
    if exc.code == "deadline_exceeded":
        return writer.event(
            "research.interrupted",
            ResearchInterrupted(run_id=run_id, code="deadline_exceeded"),
            terminal=True,
        )
    if exc.code == "interrupted":
        return writer.event(
            "research.interrupted",
            ResearchInterrupted(run_id=run_id, code="interrupted"),
            terminal=True,
        )
    return writer.event(
        "research.failed",
        ResearchFailed(run_id=run_id, code=_safe_failure_code(exc.code)),
        terminal=True,
    )


def _safe_failure_code(code: str) -> str:
    if code in {
        "model_refused",
        "provider_not_configured",
        "provider_timeout",
        "provider_rate_limited",
        "provider_unavailable",
        "invalid_model_output",
        "invalid_state",
        "search_failed",
        "checkpoint_unavailable",
        "history_unavailable",
    }:
        return code
    return "provider_unavailable"


async def _quiet(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError, Exception:
        return
