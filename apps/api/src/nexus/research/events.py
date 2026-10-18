"""Public research stream events. Internal graph state is not a field."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from nexus.schemas.report import ReportStatus

StageName = Literal[
    "planning",
    "discovery",
    "ingestion",
    "retrieval",
    "evaluation",
    "synthesis",
]


class ProgressCounts(BaseModel):
    """Allowlisted counters. Passage text and URLs are not included."""

    model_config = ConfigDict(extra="forbid")

    pages_indexed: int = Field(ge=0, le=10000)
    search_calls: int = Field(ge=0, le=10000)


class ResearchStarted(BaseModel):
    """The domain row exists and the graph has not finished."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    run_id: UUID
    research_id: UUID
    status: Literal["running"] = "running"


class ResearchProgress(BaseModel):
    """One completed graph node. ``message`` is a server label, not model text."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    run_id: UUID
    stage: StageName
    iteration: int = Field(ge=0, le=10)
    message: str = Field(min_length=1, max_length=80)
    counts: ProgressCounts


class ResearchCompleted(BaseModel):
    """Sent only after the domain report commit succeeds."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    run_id: UUID
    research_id: UUID
    status: ReportStatus


class ResearchFailed(BaseModel):
    """A terminal failure. ``code`` is a stable machine code."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    run_id: UUID
    code: str = Field(pattern=r"^[a-z0-9_]{1,64}$")


class ResearchInterrupted(BaseModel):
    """The run stopped because of a deadline or a dropped request."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    run_id: UUID
    code: Literal["interrupted", "deadline_exceeded"]
