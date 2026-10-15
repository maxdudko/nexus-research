"""The planner validates model output and hides provider details."""

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai.chat_models.base import OpenAIRefusalError
from openai import APITimeoutError, RateLimitError
from tests.conftest import make_settings

from nexus.ai.errors import ResearchError
from nexus.ai.planner import Planner
from nexus.ai.prompts import PLANNER_SYSTEM_PROMPT
from nexus.schemas.research import ResearchPlan, ResearchTask

SECRET = "sk-test-openai"
QUESTION = "How do AI coding agents improve software development workflows?"


class FakeModel:
    def __init__(self, result: object) -> None:
        self._result = result
        self.messages: list[object] = []

    async def ainvoke(self, messages: list[object]) -> object:
        self.messages = list(messages)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class SecretTimeout(APITimeoutError):
    def __init__(self) -> None:
        Exception.__init__(self, f"{SECRET} {PLANNER_SYSTEM_PROMPT}")


class SecretRateLimit(RateLimitError):
    def __init__(self) -> None:
        Exception.__init__(self, SECRET)


def _planner(result: object) -> tuple[Planner, FakeModel]:
    model = FakeModel(result)
    settings = make_settings(openai_api_key=SECRET)
    return Planner(settings, model=model), model


def _valid_plan() -> ResearchPlan:
    return ResearchPlan(
        objective="Understand agents",
        tasks=[
            ResearchTask(
                question="Which activities benefit?",
                search_queries=["AI coding agents workflows"],
            )
        ],
    )


async def test_valid_plan_keeps_system_and_human_messages_separate() -> None:
    planner, model = _planner(_valid_plan())

    plan = await planner.create_plan(QUESTION)

    assert plan == _valid_plan()
    assert len(model.messages) == 2
    assert isinstance(model.messages[0], SystemMessage)
    assert isinstance(model.messages[1], HumanMessage)
    assert model.messages[0].content == PLANNER_SYSTEM_PROMPT
    assert model.messages[1].content == QUESTION
    assert QUESTION not in str(model.messages[0].content)


async def test_too_many_tasks_is_rejected_without_echoing_the_secret() -> None:
    task = {
        "question": "Which activities benefit?",
        "search_queries": ["AI coding agents workflows"],
    }
    planner, _model = _planner({"objective": SECRET, "tasks": [task, task, task, task]})

    with pytest.raises(ResearchError) as exc_info:
        await planner.create_plan(QUESTION)

    error = exc_info.value
    assert error.code == "invalid_model_output"
    assert error.status_code == 503
    assert SECRET not in error.message
    assert PLANNER_SYSTEM_PROMPT not in error.message


async def test_empty_output_is_invalid() -> None:
    planner, _model = _planner(None)

    with pytest.raises(ResearchError) as exc_info:
        await planner.create_plan(QUESTION)

    assert exc_info.value.code == "invalid_model_output"
    assert exc_info.value.status_code == 503


async def test_refusal_does_not_reveal_the_refusal_text() -> None:
    refusal = OpenAIRefusalError(f"{SECRET} {PLANNER_SYSTEM_PROMPT}")
    planner, _model = _planner(refusal)

    with pytest.raises(ResearchError) as exc_info:
        await planner.create_plan(QUESTION)

    error = exc_info.value
    assert error.code == "model_refused"
    assert error.status_code == 503
    assert SECRET not in error.message
    assert PLANNER_SYSTEM_PROMPT not in error.message


async def test_timeout_does_not_reveal_the_provider_text() -> None:
    planner, _model = _planner(SecretTimeout())

    with pytest.raises(ResearchError) as exc_info:
        await planner.create_plan(QUESTION)

    error = exc_info.value
    assert error.code == "provider_timeout"
    assert error.status_code == 504
    assert SECRET not in error.message
    assert PLANNER_SYSTEM_PROMPT not in error.message


async def test_rate_limit_uses_429() -> None:
    planner, _model = _planner(SecretRateLimit())

    with pytest.raises(ResearchError) as exc_info:
        await planner.create_plan(QUESTION)

    error = exc_info.value
    assert error.code == "provider_rate_limited"
    assert error.status_code == 429
    assert SECRET not in error.message
