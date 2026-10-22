"""Read a private request only after the session is valid."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.datastructures import FormData, UploadFile

from nexus.ai.errors import not_found
from nexus.api.deps import CurrentUser, get_current_user
from nexus.identity.scopes import mint_scope, owned_scope
from nexus.schemas.evidence import SearchRequest, WebIngestionRequest
from nexus.schemas.research import ResearchQuestionRequest, ResearchRunRequest


@dataclass(frozen=True)
class OwnedRun:
    """A question whose scope already belongs to the caller."""

    user: CurrentUser
    research_id: UUID
    question: str


@dataclass(frozen=True)
class OwnedEvidence:
    """An existing scope the caller owns."""

    user: CurrentUser
    research_id: UUID
    web: WebIngestionRequest | None = None
    search: SearchRequest | None = None
    upload: UploadFile | None = None


async def read_question(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    request: Request,
) -> ResearchQuestionRequest:
    """Parse a question after authentication, so an anonymous caller gets 401."""
    body = await _model(request, ResearchQuestionRequest)
    await request.app.state.limiter.charge_billable(current.id)
    return body


async def resolve_run(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    request: Request,
) -> OwnedRun:
    """Mint a scope or accept one this user already owns. Never insert a miss."""
    body = await _model(request, ResearchRunRequest)
    limiter = request.app.state.limiter
    await limiter.charge_billable(current.id)
    factory = request.app.state.session_factory
    if body.research_id is None:
        await limiter.charge_mint(current.id)
        research_id = await mint_scope(factory, current.id, datetime.now(UTC))
    else:
        found = await owned_scope(factory, current.id, body.research_id)
        if found is None:
            raise not_found()
        research_id = found
    return OwnedRun(user=current, research_id=research_id, question=body.question)


async def authorize_pdf(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    request: Request,
) -> OwnedEvidence:
    """Require an owned scope before the upload is extracted."""
    form = await request.form()
    research_id = _form_uuid(form, "research_id")
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise _invalid(("file",))
    await request.app.state.limiter.charge_billable(current.id)
    await _require_owned(request, current, research_id)
    return OwnedEvidence(user=current, research_id=research_id, upload=upload)


async def authorize_web(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    request: Request,
) -> OwnedEvidence:
    """Require an owned scope before a page is fetched."""
    body = await _model(request, WebIngestionRequest)
    await request.app.state.limiter.charge_billable(current.id)
    await _require_owned(request, current, body.research_id)
    return OwnedEvidence(user=current, research_id=body.research_id, web=body)


async def authorize_search(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    request: Request,
) -> OwnedEvidence:
    """Require an owned scope before a query is embedded."""
    body = await _model(request, SearchRequest)
    await request.app.state.limiter.charge_billable(current.id)
    await _require_owned(request, current, body.research_id)
    return OwnedEvidence(user=current, research_id=body.research_id, search=body)


async def _require_owned(
    request: Request,
    current: CurrentUser,
    research_id: UUID,
) -> None:
    found = await owned_scope(
        request.app.state.session_factory,
        current.id,
        research_id,
    )
    if found is None:
        raise not_found()


def _form_uuid(form: FormData, name: str) -> UUID:
    raw = form.get(name)
    if not isinstance(raw, str):
        raise _invalid((name,))
    try:
        return UUID(raw)
    except ValueError:
        raise _invalid((name,)) from None


async def _model[T: BaseModel](request: Request, model: type[T]) -> T:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise RequestValidationError(
            [
                {
                    "type": "json_invalid",
                    "loc": ("body",),
                    "msg": "Invalid JSON",
                    "input": None,
                }
            ]
        ) from None
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        errors = []
        for error in exc.errors():
            location = tuple(error.get("loc", ()))
            errors.append({**error, "loc": ("body", *location)})
        raise RequestValidationError(errors) from None


def _invalid(location: tuple[str, ...]) -> RequestValidationError:
    return RequestValidationError(
        [
            {
                "type": "missing",
                "loc": ("body", *location),
                "msg": "Field required",
                "input": None,
            }
        ]
    )
