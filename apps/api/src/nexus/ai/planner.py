"""Request-scoped research planning. The route does not call the model."""

from typing import Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from nexus.ai.client import OpenAIStructuredModel
from nexus.ai.errors import ResearchError, invalid_model_output, map_provider_exception
from nexus.ai.prompts import PLANNER_SYSTEM_PROMPT
from nexus.core.config import Settings
from nexus.schemas.research import ResearchPlan


class StructuredModel(Protocol):
    """The only model method the planner needs."""

    async def ainvoke(self, messages: list[BaseMessage]) -> object: ...


class Planner:
    """Ask a structured model for a plan and reject invalid output."""

    def __init__(
        self,
        settings: Settings,
        model: StructuredModel | None = None,
    ) -> None:
        self._settings = settings
        self._model = model

    async def create_plan(self, question: str) -> ResearchPlan:
        model = self._model or OpenAIStructuredModel(self._settings)
        messages: list[BaseMessage] = [
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=question),
        ]
        try:
            result = await model.ainvoke(messages)
            return _coerce_plan(result)
        except ResearchError:
            raise
        except Exception as exc:
            raise map_provider_exception(exc) from None


def _coerce_plan(result: object) -> ResearchPlan:
    if isinstance(result, ResearchPlan):
        return result
    if isinstance(result, dict):
        try:
            return ResearchPlan.model_validate(result)
        except ValidationError:
            raise invalid_model_output() from None
    raise invalid_model_output()
