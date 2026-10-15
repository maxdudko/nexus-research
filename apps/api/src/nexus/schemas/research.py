"""HTTP and provider contracts for a research plan."""

from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator


class ResearchQuestionRequest(BaseModel):
    """A research question supplied by the caller."""

    question: str = Field(min_length=10, max_length=2000)

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


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
                if len(trimmed) > 200:
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
