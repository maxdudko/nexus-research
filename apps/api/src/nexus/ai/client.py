"""Lazy OpenAI chat model construction. Importing this module calls nothing."""

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from nexus.ai.errors import provider_not_configured
from nexus.core.config import Settings
from nexus.schemas.research import ResearchPlan


def build_chat_model(settings: Settings) -> ChatOpenAI:
    """Build a chat model from process settings. A missing key raises."""
    key = settings.openai_api_key
    if key is None:
        raise provider_not_configured()
    # ChatOpenAI stores this SecretStr and reads it when the client is built.
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=key,
        timeout=settings.ai_timeout_seconds,
        max_retries=settings.ai_max_retries,
    )


class OpenAIStructuredModel:
    """Planner-facing wrapper around structured output. Not a provider framework."""

    def __init__(self, settings: Settings) -> None:
        chat = build_chat_model(settings)
        self._runnable = chat.with_structured_output(
            ResearchPlan,
            method="json_schema",
            strict=True,
        )

    async def ainvoke(self, messages: list[BaseMessage]) -> object:
        return await self._runnable.ainvoke(messages)


class StructuredOutputModel:
    """Structured output for one schema. The planner keeps its own wrapper."""

    def __init__(self, settings: Settings, schema: type[BaseModel]) -> None:
        chat = build_chat_model(settings)
        self._runnable = chat.with_structured_output(
            schema,
            method="json_schema",
            strict=True,
        )

    async def ainvoke(self, messages: list[BaseMessage]) -> object:
        return await self._runnable.ainvoke(messages)
