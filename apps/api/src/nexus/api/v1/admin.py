"""Read-only evaluation history. Diagnostics and usage are not on this router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.admin.comparison import compare_runs, view_from_detail
from nexus.admin.evaluation_store import (
    EvaluationUnreadable,
    get_evaluation,
    list_cases,
    list_evaluations,
)
from nexus.admin.schemas import (
    ComparisonResponse,
    EvaluationCasePage,
    EvaluationDetail,
    EvaluationListResponse,
)
from nexus.ai.errors import ResearchError, invalid_request
from nexus.api.deps import CurrentUser, require_admin

router = APIRouter()
_CATEGORIES = frozenset({"retrieval", "citation", "grounding"})


def _not_found() -> ResearchError:
    return ResearchError("not_found", 404, "The evaluation was not found.")


def _unreadable() -> ResearchError:
    return ResearchError(
        "evaluation_unreadable",
        500,
        "The evaluation could not be read.",
    )


def _factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def _limit(value: str | None) -> int:
    if value is None:
        return 20
    if not value.isdigit():
        raise invalid_request("limit")
    parsed = int(value)
    if not 1 <= parsed <= 50:
        raise invalid_request("limit")
    return parsed


def _category(value: str | None) -> str | None:
    if value is None:
        return None
    if value not in _CATEGORIES:
        raise invalid_request("category")
    return value


def _uuid(value: str | None, field: str) -> UUID:
    if not value:
        raise invalid_request(field)
    try:
        parsed = UUID(value)
    except ValueError:
        raise invalid_request(field) from None
    if str(parsed) != value:
        raise invalid_request(field)
    return parsed


@router.get("/evaluations", response_model=EvaluationListResponse)
async def list_admin_evaluations(
    current: Annotated[CurrentUser, Depends(require_admin)],
    request: Request,
    limit: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> EvaluationListResponse:
    """List imported evaluations. The response has no cases and no matrix."""
    del current
    try:
        return await list_evaluations(_factory(request), _limit(limit), cursor)
    except EvaluationUnreadable:
        raise _unreadable() from None


@router.get("/evaluations/compare", response_model=ComparisonResponse)
async def compare_admin_evaluations(
    current: Annotated[CurrentUser, Depends(require_admin)],
    request: Request,
    left_id: Annotated[str | None, Query()] = None,
    right_id: Annotated[str | None, Query()] = None,
) -> ComparisonResponse:
    """Compare two stored runs. Deltas are omitted when the pair is incompatible."""
    del current
    left_key = _uuid(left_id, "left_id")
    right_key = _uuid(right_id, "right_id")
    try:
        left = await get_evaluation(_factory(request), left_key)
        right = await get_evaluation(_factory(request), right_key)
    except EvaluationUnreadable:
        raise _unreadable() from None
    if left is None or right is None:
        raise _not_found()
    result = compare_runs(view_from_detail(left), view_from_detail(right))
    return ComparisonResponse(
        comparable=result.comparable,
        reasons=list(result.reasons),
        warnings=list(result.warnings),
        cost_comparable=result.cost_comparable,
        pricing_reason=result.pricing_reason,
        left=left,
        right=right,
        deltas=result.deltas,
    )


@router.get("/evaluations/{evaluation_id}", response_model=EvaluationDetail)
async def get_admin_evaluation(
    current: Annotated[CurrentUser, Depends(require_admin)],
    request: Request,
    evaluation_id: UUID,
) -> EvaluationDetail:
    """Return provenance and summary. Offline runs have no confusion matrix."""
    del current
    try:
        found = await get_evaluation(_factory(request), evaluation_id)
    except EvaluationUnreadable:
        raise _unreadable() from None
    if found is None:
        raise _not_found()
    return found


@router.get(
    "/evaluations/{evaluation_id}/cases",
    response_model=EvaluationCasePage,
)
async def list_admin_evaluation_cases(
    current: Annotated[CurrentUser, Depends(require_admin)],
    request: Request,
    evaluation_id: UUID,
    limit: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
) -> EvaluationCasePage:
    """Return sanitized cases for one evaluation."""
    del current
    try:
        page = await list_cases(
            _factory(request),
            evaluation_id,
            _limit(limit),
            cursor,
            _category(category),
        )
    except EvaluationUnreadable:
        raise _unreadable() from None
    if page is None:
        raise _not_found()
    return page
