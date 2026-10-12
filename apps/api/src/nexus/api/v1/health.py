"""Liveness route. This handler performs no I/O."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class LiveStatus(BaseModel):
    status: Literal["ok"]


@router.get("/live")
async def live() -> LiveStatus:
    return LiveStatus(status="ok")
