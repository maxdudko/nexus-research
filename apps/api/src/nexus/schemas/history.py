"""Read models for persisted research runs. Reports stay off the list."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from nexus.schemas.report import GraphUsage, ResearchReport

RunStatus = Literal[
    "running",
    "completed",
    "partial",
    "insufficient_evidence",
    "failed",
    "interrupted",
]


class RunSummary(BaseModel):
    """One history row. The report body is loaded from the detail route."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    research_id: UUID
    question: str
    status: RunStatus
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None


class RunDetail(BaseModel):
    """A stored run. ``running`` can be stale after a crash and is not rewritten."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    research_id: UUID
    question: str
    status: RunStatus
    error_code: str | None
    warnings: list[str]
    usage: GraphUsage | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class RunListResponse(BaseModel):
    """A page of runs ordered by ``created_at DESC, run_id DESC``."""

    model_config = ConfigDict(extra="forbid")

    items: list[RunSummary]
    next_cursor: str | None


class RunDetailResponse(BaseModel):
    """A run plus its report. Failed and interrupted runs have no report."""

    model_config = ConfigDict(extra="forbid")

    run: RunDetail
    report: ResearchReport | None = Field(default=None)
