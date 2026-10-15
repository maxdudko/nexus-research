"""Research routes. Handlers stay thin and call a planner."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from nexus.ai.planner import Planner
from nexus.core.config import Settings
from nexus.schemas.research import ResearchPlan, ResearchQuestionRequest

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
