"""Research routes. Handlers stay thin and call a planner or one graph run."""

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from nexus.ai.errors import checkpoint_unavailable
from nexus.ai.planner import Planner
from nexus.core.config import Settings
from nexus.research.checkpoint import CheckpointRuntime, open_checkpointer
from nexus.research.discovery import Discovery
from nexus.research.runner import ResearchRunner, production_context
from nexus.retrieval.vector_store import EvidenceVectorStore
from nexus.schemas.research import (
    DiscoveryResult,
    ResearchPlan,
    ResearchQuestionRequest,
    ResearchRunRequest,
    ResearchRunResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_planner(request: Request) -> Planner:
    """Build a planner for this request. The model is created when planning."""
    settings: Settings = request.app.state.settings
    return Planner(settings)


@router.post("/plan", response_model=ResearchPlan)
async def create_research_plan(
    body: ResearchQuestionRequest,
    planner: Annotated[Planner, Depends(get_planner)],
) -> ResearchPlan:
    return await planner.create_plan(body.question)


def get_discovery(request: Request) -> Discovery:
    """Build a discovery run for this request. Providers are created on use."""
    settings: Settings = request.app.state.settings
    return Discovery(settings)


@router.post("/discover", response_model=DiscoveryResult)
async def discover_research(
    body: ResearchQuestionRequest,
    discovery: Annotated[Discovery, Depends(get_discovery)],
) -> DiscoveryResult:
    return await discovery.discover(body.question)


async def get_runner(request: Request) -> AsyncIterator[ResearchRunner]:
    """Open the shared checkpointer on first use and close the vector client."""
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


@router.post("/run", response_model=ResearchRunResponse)
async def run_research(
    body: ResearchRunRequest,
    runner: Annotated[ResearchRunner, Depends(get_runner)],
) -> ResearchRunResponse:
    """Run one question to a report. The request waits, and nothing is queued."""
    return await runner.run(body.question, body.research_id)
