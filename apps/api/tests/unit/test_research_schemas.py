"""Request bounds and provider-schema limits for a research plan."""

import json

import pytest
from pydantic import ValidationError

from nexus.schemas.research import ResearchPlan, ResearchQuestionRequest, ResearchTask


def _task(query_count: int = 1, query: str = "AI coding agents") -> ResearchTask:
    return ResearchTask(
        question="Which activities benefit?",
        search_queries=[query] * query_count,
    )


def test_question_is_stripped_before_length_check() -> None:
    body = ResearchQuestionRequest(question="  How do agents help?  ")
    assert body.question == "How do agents help?"


@pytest.mark.parametrize("question", ["short", "   short   ", "x" * 2001, "   "])
def test_question_outside_bounds_fails(question: str) -> None:
    with pytest.raises(ValidationError):
        ResearchQuestionRequest(question=question)


def test_question_at_bounds_is_accepted() -> None:
    short = ResearchQuestionRequest(question="x" * 10)
    long = ResearchQuestionRequest(question="y" * 2000)
    assert short.question == "x" * 10
    assert long.question == "y" * 2000


def test_plan_trims_text() -> None:
    plan = ResearchPlan(
        objective="  Understand agents  ",
        tasks=[
            ResearchTask(
                question="  Which activities benefit?  ",
                search_queries=["  AI coding agents  "],
            )
        ],
    )
    assert plan.objective == "Understand agents"
    assert plan.tasks[0].question == "Which activities benefit?"
    assert plan.tasks[0].search_queries == ["AI coding agents"]


def test_query_at_200_characters_is_accepted() -> None:
    plan = ResearchPlan(objective="Understand agents", tasks=[_task(query="q" * 200)])
    assert plan.tasks[0].search_queries == ["q" * 200]


@pytest.mark.parametrize(
    "plan_kwargs",
    [
        {"objective": "Understand agents", "tasks": []},
        {"objective": "Understand agents", "tasks": [_task() for _ in range(4)]},
        {"objective": "Understand agents", "tasks": [_task(query_count=4)]},
        {"objective": "Understand agents", "tasks": [_task(query="q" * 201)]},
        {
            "objective": "Understand agents",
            "tasks": [_task(query_count=3) for _ in range(3)] + [_task()],
        },
        {"objective": "   ", "tasks": [_task()]},
        {
            "objective": "Understand agents",
            "tasks": [ResearchTask(question="   ", search_queries=["AI agents"])],
        },
        {
            "objective": "Understand agents",
            "tasks": [
                ResearchTask(question="Which activities?", search_queries=["  "]),
            ],
        },
    ],
)
def test_plan_bounds_reject_invalid_output(plan_kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResearchPlan(**plan_kwargs)  # type: ignore[arg-type]


def test_provider_schema_has_no_length_constraints_or_defaults() -> None:
    schema = ResearchPlan.model_json_schema()
    encoded = json.dumps(schema)
    assert "minLength" not in encoded
    assert "maxLength" not in encoded

    def walk(node: object) -> None:
        if isinstance(node, dict):
            assert "default" not in node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
