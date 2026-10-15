"""Research routes. Handlers stay thin and call a planner."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from nexus.ai.planner import Planner
from nexus.core.config import Settings
from nexus.research.discovery import Discovery
from nexus.schemas.research import (
    DiscoveryResult,
    ResearchPlan,
    ResearchQuestionRequest,
)

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
