"""Read API for domain research history. Checkpoint tables are not queried."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.ai.errors import not_found
from nexus.api.deps import CurrentUser, get_current_user
from nexus.research.history import ResearchHistory
from nexus.schemas.history import RunDetailResponse, RunListResponse

router = APIRouter()


def get_history(
    request: Request,
    current: Annotated[CurrentUser, Depends(get_current_user)],
) -> ResearchHistory:
    """Open short transactions after the caller is authenticated."""
    del current
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return ResearchHistory(factory)


@router.get("/runs", response_model=RunListResponse)
async def list_research_runs(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    history: Annotated[ResearchHistory, Depends(get_history)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> RunListResponse:
    """List this user's runs. A stale ``running`` row is returned as stored."""
    return await history.list_runs(limit, cursor, owner_id=current.id)


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
async def get_research_run(
    run_id: UUID,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    history: Annotated[ResearchHistory, Depends(get_history)],
) -> RunDetailResponse:
    """Return one owned run and, when it succeeded, its validated report."""
    found = await history.get_run(run_id, owner_id=current.id)
    if found is None:
        raise not_found()
    return found
