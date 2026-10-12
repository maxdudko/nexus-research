"""Health routes. Liveness performs no I/O."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.status import HTTP_200_OK, HTTP_503_SERVICE_UNAVAILABLE

from nexus.core.config import Settings
from nexus.infrastructure.database import postgres_is_ready
from nexus.infrastructure.qdrant import qdrant_is_ready

router = APIRouter()


class LiveStatus(BaseModel):
    status: Literal["ok"]


class ReadyStatus(BaseModel):
    status: Literal["ok", "unavailable"]
    postgres: Literal["ok", "unavailable"]
    qdrant: Literal["ok", "unavailable"]


@router.get("/live")
async def live() -> LiveStatus:
    return LiveStatus(status="ok")


async def get_ready_status(request: Request) -> ReadyStatus:
    """Check PostgreSQL and Qdrant without constructing clients in the route."""
    settings: Settings = request.app.state.settings
    timeout = settings.readiness_timeout_seconds
    postgres_ok, qdrant_ok = await asyncio.gather(
        postgres_is_ready(str(settings.database_url), timeout),
        qdrant_is_ready(str(settings.qdrant_url), timeout),
    )
    postgres: Literal["ok", "unavailable"] = "ok" if postgres_ok else "unavailable"
    qdrant: Literal["ok", "unavailable"] = "ok" if qdrant_ok else "unavailable"
    overall: Literal["ok", "unavailable"] = (
        "ok" if postgres_ok and qdrant_ok else "unavailable"
    )
    return ReadyStatus(status=overall, postgres=postgres, qdrant=qdrant)


@router.get(
    "/ready",
    response_model=ReadyStatus,
    responses={
        HTTP_200_OK: {"model": ReadyStatus},
        HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyStatus},
    },
)
async def ready(
    result: Annotated[ReadyStatus, Depends(get_ready_status)],
) -> JSONResponse:
    status_code = HTTP_200_OK if result.status == "ok" else HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=result.model_dump())
