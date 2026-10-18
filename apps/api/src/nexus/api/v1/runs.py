"""Read API for domain research history. Checkpoint tables are not queried."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.ai.errors import not_found
from nexus.research.history import ResearchHistory
from nexus.schemas.history import RunDetailResponse, RunListResponse

router = APIRouter()


def get_history(request: Request) -> ResearchHistory:
    """Open short transactions on the application session factory."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return ResearchHistory(factory)


@router.get("/runs", response_model=RunListResponse)
async def list_research_runs(
    history: Annotated[ResearchHistory, Depends(get_history)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> RunListResponse:
    """List recent runs. A stale ``running`` row is returned as stored."""
    return await history.list_runs(limit, cursor)


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
async def get_research_run(
    run_id: UUID,
    history: Annotated[ResearchHistory, Depends(get_history)],
) -> RunDetailResponse:
    """Return one run and, when the run succeeded, its validated report."""
    found = await history.get_run(run_id)
    if found is None:
        raise not_found()
    return found
