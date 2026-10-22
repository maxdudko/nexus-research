"""Research routes. Handlers stay thin and call a planner or one graph run."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from nexus.ai.errors import (
    ResearchError,
    checkpoint_unavailable,
    history_unavailable,
    too_many_requests,
)
from nexus.ai.planner import Planner
from nexus.api.deps import CurrentUser, get_current_user
from nexus.api.ownership import OwnedRun, read_question, resolve_run
from nexus.api.v1.runs import get_history
from nexus.core.config import Settings
from nexus.identity.scopes import mint_scope
from nexus.research.checkpoint import CheckpointRuntime, open_checkpointer
from nexus.research.coordinator import RunCoordinator
from nexus.research.discovery import Discovery
from nexus.research.history import ResearchHistory
from nexus.research.runner import ResearchRunner, production_context
from nexus.retrieval.vector_store import EvidenceVectorStore
from nexus.schemas.research import (
    DiscoveryResult,
    ResearchPlan,
    ResearchQuestionRequest,
    ResearchRunResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_planner(
    request: Request,
    question: Annotated[ResearchQuestionRequest, Depends(read_question)],
) -> Planner:
    """Build a planner after the caller is authenticated."""
    del question
    settings: Settings = request.app.state.settings
    return Planner(settings)


@router.post("/plan", response_model=ResearchPlan)
async def create_research_plan(
    question: Annotated[ResearchQuestionRequest, Depends(read_question)],
    planner: Annotated[Planner, Depends(get_planner)],
) -> ResearchPlan:
    return await planner.create_plan(question.question)


def get_discovery(
    request: Request,
    question: Annotated[ResearchQuestionRequest, Depends(read_question)],
) -> Discovery:
    """Build a discovery run after the caller is authenticated."""
    del question
    settings: Settings = request.app.state.settings
    return Discovery(settings)


@router.post("/discover", response_model=DiscoveryResult)
async def discover_research(
    question: Annotated[ResearchQuestionRequest, Depends(read_question)],
    discovery: Annotated[Discovery, Depends(get_discovery)],
) -> DiscoveryResult:
    return await discovery.discover(question.question)


async def get_runner(
    request: Request,
    owned: Annotated[OwnedRun, Depends(resolve_run)],
) -> AsyncIterator[ResearchRunner]:
    """Open clients only after the scope belongs to the caller."""
    settings: Settings = request.app.state.settings
    runtime = await _checkpoint(request)
    store = EvidenceVectorStore(settings)
    try:
        yield ResearchRunner(
            graph=runtime.graph,
            context=production_context(
                settings,
                request.app.state.session_factory,
                store,
                owner_user_id=owned.user.id,
            ),
        )
    finally:
        await store.close()


async def _checkpoint(request: Request) -> CheckpointRuntime:
    current = request.app.state.checkpoint
    if isinstance(current, CheckpointRuntime):
        return current
    async with request.app.state.checkpoint_lock:
        current = request.app.state.checkpoint
        if isinstance(current, CheckpointRuntime):
            return current
        try:
            opened = await open_checkpointer(
                str(request.app.state.settings.database_url)
            )
        except Exception as exc:
            logger.warning("checkpoint pool failed: %s", type(exc).__name__)
            raise checkpoint_unavailable() from None
        request.app.state.checkpoint = opened
        return opened


@router.post("/scopes")
async def create_research_scope(
    request: Request,
    current: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict[str, str]:
    """Mint a research id for this user. The client does not supply one."""
    research_id = await mint_scope(
        request.app.state.session_factory,
        current.id,
        datetime.now(UTC),
    )
    return {"research_id": str(research_id)}


@router.post("/run", response_model=ResearchRunResponse)
async def run_research(
    owned: Annotated[OwnedRun, Depends(resolve_run)],
    runner: Annotated[ResearchRunner, Depends(get_runner)],
) -> ResearchRunResponse:
    """Run one question to a report. The request waits, and nothing is queued."""
    return await runner.run(owned.question, owned.research_id)


@router.post("/stream")
async def stream_research(
    owned: Annotated[OwnedRun, Depends(resolve_run)],
    request: Request,
    runner: Annotated[ResearchRunner, Depends(get_runner)],
    history: Annotated[ResearchHistory, Depends(get_history)],
) -> StreamingResponse:
    """Stream one request-scoped run. Disconnect cancels that run."""
    settings: Settings = request.app.state.settings
    semaphore: asyncio.Semaphore = request.app.state.stream_semaphore
    held = await _acquire(semaphore)
    if not held:
        raise too_many_requests()
    run_id = uuid.uuid4()
    try:
        await history.create_running(
            run_id,
            owned.research_id,
            owned.question,
            runner.clock(),
            owner_id=owned.user.id,
        )
    except ResearchError:
        semaphore.release()
        raise
    except Exception as exc:
        semaphore.release()
        logger.warning("could not store a research run: %s", type(exc).__name__)
        raise history_unavailable() from None
    coordinator = RunCoordinator(
        runner=runner,
        history=history,
        question=owned.question,
        research_id=owned.research_id,
        run_id=run_id,
        heartbeat_seconds=settings.stream_heartbeat_seconds,
        cancel_persist_seconds=settings.stream_cancel_persist_seconds,
        clock=runner.clock,
    )

    async def frames() -> AsyncIterator[str]:
        try:
            async for frame in coordinator.events():
                yield frame
        finally:
            semaphore.release()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


async def _acquire(semaphore: asyncio.Semaphore) -> bool:
    """Take one stream permit without waiting for a running graph to finish."""
    try:
        await asyncio.wait_for(semaphore.acquire(), 0.01)
    except TimeoutError:
        return False
    return True
