"""HTTP and provider contracts for research planning, discovery, and runs."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nexus.schemas.report import GraphUsage, ReportStatus, ResearchReport

QUERY_MAX_LENGTH = 200


class ResearchQuestionRequest(BaseModel):
    """A research question supplied by the caller."""

    question: str = Field(min_length=10, max_length=2000)

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class ResearchRunRequest(BaseModel):
    """One research run. The server chooses the thread id."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=10, max_length=2000)
    research_id: UUID | None = None

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class ResearchRunResponse(BaseModel):
    """A finished report. Failed and interrupted runs use the error envelope."""

    model_config = ConfigDict(extra="forbid")

    research_id: UUID
    run_id: UUID
    status: ReportStatus
    report: ResearchReport
    warnings: list[str]
    usage: GraphUsage


class ResearchTask(BaseModel):
    """One planning task. Bounds are checked on the parent plan."""

    question: str
    search_queries: list[str]


class ResearchPlan(BaseModel):
    """A structured plan. Length limits stay out of the provider schema."""

    objective: str
    tasks: list[ResearchTask]

    @model_validator(mode="after")
    def enforce_bounds(self) -> Self:
        objective = self.objective.strip()
        if not objective:
            raise ValueError("objective must not be empty")

        tasks: list[ResearchTask] = []
        total_queries = 0
        for task in self.tasks:
            question = task.question.strip()
            if not question:
                raise ValueError("task question must not be empty")
            queries: list[str] = []
            for query in task.search_queries:
                trimmed = query.strip()
                if not trimmed:
                    raise ValueError("search query must not be empty")
                if len(trimmed) > QUERY_MAX_LENGTH:
                    raise ValueError("search query must be at most 200 characters")
                queries.append(trimmed)
            if not 1 <= len(queries) <= 3:
                raise ValueError("each task must contain 1 to 3 search queries")
            total_queries += len(queries)
            tasks.append(ResearchTask(question=question, search_queries=queries))

        if total_queries > 9:
            raise ValueError("total search queries must be at most 9")
        if not 1 <= len(tasks) <= 3:
            raise ValueError("tasks must contain 1 to 3 items")
        # Pydantic 2.14 keeps `self` when `__init__` runs this validator.
        self.objective = objective
        self.tasks = tasks
        return self


class SearchResult(BaseModel):
    """One normalized search hit. This contract is not sent to the model."""

    title: str
    url: str
    snippet: str
    published_at: datetime | None = None


class DiscoveryResult(BaseModel):
    """A bounded discovery response. Sources come from the search adapter."""

    status: Literal["completed", "partial"]
    summary: str
    sources: list[SearchResult]
    search_calls: int
    llm_calls: int
    warnings: list[str]
