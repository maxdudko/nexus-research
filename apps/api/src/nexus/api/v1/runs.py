"""Read API for domain research history. Checkpoint tables are not queried."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.ai.errors import not_found, run_in_progress
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


@router.delete("/runs/{run_id}")
async def delete_research_run(
    run_id: UUID,
    request: Request,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    history: Annotated[ResearchHistory, Depends(get_history)],
) -> dict[str, str]:
    """Remove one owned run and its report.

    Sources, Qdrant evidence and checkpoints are kept. A run this process is
    still streaming returns 409; a stale ``running`` row can be removed.
    """
    if await request.app.state.limiter.run_is_active(run_id):
        # Ownership first, so another account's live run stays a 404.
        if await history.get_run(run_id, owner_id=current.id) is None:
            raise not_found()
        raise run_in_progress()
    deleted = await history.delete_run(run_id, owner_id=current.id)
    if not deleted:
        raise not_found()
    return {"status": "ok"}
