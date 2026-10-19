"""Provider failure telemetry. Logical search counts stay separate from retries."""

import pytest
from langchain_core.messages import AIMessage
from tavily.errors import TimeoutError as TavilyTimeoutError
from tests.conftest import make_settings

from nexus.observability.context import bind_run
from nexus.research.discovery import Discovery
from nexus.tools.web_search import WebSearch

QUESTION = "How do AI coding agents improve software development workflows?"


async def test_tavily_retry_keeps_one_logical_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def search_fn(_query: str, _max_results: int) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TavilyTimeoutError(1.0)
        return {
            "results": [
                {
                    "title": "Notes",
                    "url": "https://example.org/notes",
                    "content": "Review notes",
                }
            ]
        }

    async def sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("nexus.tools.web_search.asyncio.sleep", sleep)
    settings = make_settings(
        openai_api_key="sk-test-openai",
        tavily_api_key="tvly-test",
        ai_max_retries=1,
    )
    discovery = Discovery(
        settings,
        model=_ScriptedModel(),
        search=WebSearch(settings, search_fn=search_fn),
    )

    with bind_run(create_buffer=True) as buffer:
        result = await discovery.discover(QUESTION)

    assert calls["n"] == 2
    assert result.search_calls == 1
    assert buffer is not None
    attempts = [record for record in buffer.records if record.operation == "web_search"]
    assert [record.physical_attempts for record in attempts] == [1, 1]
    assert [record.outcome for record in attempts] == ["error", "ok"]


class _ScriptedModel:
    def __init__(self) -> None:
        self._replies = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "AI agents"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The notes describe code review."),
        ]

    async def ainvoke(self, _messages: list[object]) -> AIMessage:
        return self._replies.pop(0)
