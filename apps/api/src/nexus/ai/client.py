"""Lazy OpenAI chat model construction. Importing this module calls nothing."""

from collections.abc import Mapping
from typing import Protocol, cast

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from nexus.ai.errors import invalid_model_output, provider_not_configured
from nexus.core.config import Settings
from nexus.observability.spans import OperationHandle, operation
from nexus.observability.usage import sdk_attempt_count
from nexus.schemas.research import ResearchPlan


class _StructuredRunnable(Protocol):
    async def ainvoke(self, messages: list[BaseMessage]) -> object: ...


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
        self._model_name, self._runnable = _structured_runnable(settings, ResearchPlan)
        self._max_retries = settings.ai_max_retries

    async def ainvoke(self, messages: list[BaseMessage]) -> object:
        return await _invoke_structured(
            self._runnable,
            self._model_name,
            messages,
            max_retries=self._max_retries,
        )


class StructuredOutputModel:
    """Structured output for one schema. The planner keeps its own wrapper."""

    def __init__(self, settings: Settings, schema: type[BaseModel]) -> None:
        self._model_name, self._runnable = _structured_runnable(settings, schema)
        self._max_retries = settings.ai_max_retries

    async def ainvoke(self, messages: list[BaseMessage]) -> object:
        return await _invoke_structured(
            self._runnable,
            self._model_name,
            messages,
            max_retries=self._max_retries,
        )


def _structured_runnable(
    settings: Settings,
    schema: type[BaseModel],
) -> tuple[str, _StructuredRunnable]:
    chat = build_chat_model(settings)
    # include_raw keeps usage_metadata, which the parsed model does not carry.
    runnable = chat.with_structured_output(
        schema,
        method="json_schema",
        strict=True,
        include_raw=True,
    )
    return settings.openai_model, cast(_StructuredRunnable, runnable)


async def _invoke_structured(
    runnable: _StructuredRunnable,
    model_name: str,
    messages: list[BaseMessage],
    *,
    max_retries: int,
) -> object:
    with operation(
        operation="openai_chat",
        provider="openai",
        model=model_name,
    ) as handle:
        handle.physical_attempts = sdk_attempt_count(max_retries)
        result = await runnable.ainvoke(messages)
        parsed = _parsed_output(result, handle)
        if parsed is None:
            raise invalid_model_output()
        return parsed


def _parsed_output(result: object, handle: OperationHandle) -> object | None:
    """Return the validated object. Token counts come from the raw message."""
    if isinstance(result, Mapping) and "parsed" in result and "raw" in result:
        handle.observe_message(result.get("raw"))
        if result.get("parsing_error") is not None or result.get("parsed") is None:
            return None
        return result.get("parsed")
    handle.observe_message(result)
    return result
