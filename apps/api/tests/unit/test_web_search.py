"""Tavily adapter tests. The provider call is injected."""

import httpx
import pytest
from tavily.errors import BadRequestError, InvalidAPIKeyError
from tavily.errors import TimeoutError as TavilyTimeoutError
from tests.conftest import make_settings

from nexus.ai.errors import ResearchError
from nexus.tools.web_search import WebSearch


def _settings(**overrides: object):
    values: dict[str, object] = {"tavily_api_key": "tvly-test"}
    values.update(overrides)
    return make_settings(**values)


async def test_normalization_dedup_and_discarded_urls() -> None:
    payload = {
        "answer": "ignored",
        "results": [
            {
                "title": "Example",
                "url": "HTTPS://User:secret@Example.COM/a?b=1&c=2#frag",
                "content": "A snippet",
                "score": 0.9,
                "published_date": "2026-10-15T12:00:00+00:00",
                "raw_content": "full page",
            },
            {
                "title": "Duplicate",
                "url": "https://example.com/a?b=1&c=2",
                "content": "Second",
            },
            {"title": "Script", "url": "javascript:alert(1)", "content": "no"},
            {"title": "File", "url": "ftp://example.com/file", "content": "no"},
            {"title": "Missing", "content": "no url"},
            "not-a-dict",
        ],
    }

    async def search_fn(_query: str, _max_results: int) -> object:
        return payload

    results = await WebSearch(_settings(), search_fn=search_fn).search("  openai  ")

    assert len(results) == 1
    result = results[0]
    assert result.title == "Example"
    assert result.url == "https://example.com/a?b=1&c=2"
    assert result.snippet == "A snippet"
    assert result.published_at is not None
    assert result.published_at.year == 2026


async def test_unparseable_date_is_null() -> None:
    async def search_fn(_query: str, _max_results: int) -> object:
        return {
            "results": [
                {
                    "title": "News",
                    "url": "https://example.org/news",
                    "content": "Snippet",
                    "published_date": "Thursday",
                }
            ]
        }

    results = await WebSearch(_settings(), search_fn=search_fn).search("openai models")

    assert results[0].published_at is None


async def test_empty_result_list_is_valid() -> None:
    async def search_fn(_query: str, _max_results: int) -> object:
        return {"results": []}

    results = await WebSearch(_settings(), search_fn=search_fn).search("openai models")

    assert results == []


@pytest.mark.parametrize("payload", [{}, {"results": None}, {"results": "nope"}, []])
async def test_malformed_payload_is_search_failed(payload: object) -> None:
    calls = {"n": 0}

    async def search_fn(_query: str, _max_results: int) -> object:
        calls["n"] += 1
        return payload

    with pytest.raises(ResearchError) as exc_info:
        await WebSearch(_settings(ai_max_retries=2), search_fn=search_fn).search(
            "openai models",
        )

    assert calls["n"] == 1
    assert exc_info.value.code == "search_failed"


async def test_normalized_results_are_capped_at_max_results() -> None:
    async def search_fn(_query: str, _max_results: int) -> object:
        return {
            "results": [
                {
                    "title": f"Item {index}",
                    "url": f"https://example.org/{index}",
                    "content": "Snippet",
                }
                for index in range(6)
            ]
        }

    results = await WebSearch(_settings(), search_fn=search_fn).search(
        "openai models",
        max_results=5,
    )

    assert [result.url for result in results] == [
        f"https://example.org/{index}" for index in range(5)
    ]


@pytest.mark.parametrize("query", ["", "   ", "q" * 201])
async def test_invalid_query_does_not_call_the_provider(query: str) -> None:
    async def search_fn(_query: str, _max_results: int) -> object:
        raise AssertionError("provider was called")

    with pytest.raises(ValueError):
        await WebSearch(_settings(), search_fn=search_fn).search(query)


async def test_max_results_above_five_does_not_call_the_provider() -> None:
    async def search_fn(_query: str, _max_results: int) -> object:
        raise AssertionError("provider was called")

    with pytest.raises(ValueError):
        await WebSearch(_settings(), search_fn=search_fn).search(
            "openai models",
            max_results=6,
        )


async def test_missing_key_does_not_construct_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("AsyncTavilyClient was constructed")

    monkeypatch.setattr("nexus.tools.web_search.AsyncTavilyClient", fail)

    with pytest.raises(ResearchError) as exc_info:
        await WebSearch(make_settings(tavily_api_key=None)).search("openai models")

    assert exc_info.value.code == "provider_not_configured"


async def test_client_receives_snippet_search_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            captured["closed"] = True

        async def search(self, query: str, **kwargs: object) -> dict[str, object]:
            captured["query"] = query
            captured["kwargs"] = kwargs
            return {"results": []}

    monkeypatch.setattr("nexus.tools.web_search.AsyncTavilyClient", FakeClient)
    settings = _settings(ai_timeout_seconds=20, ai_max_retries=0)

    results = await WebSearch(settings).search("openai models", max_results=5)

    assert results == []
    assert captured["api_key"] == "tvly-test"
    assert captured["query"] == "openai models"
    assert captured["kwargs"] == {
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
        "timeout": 20.0,
    }
    assert captured["closed"] is True


async def test_timeout_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    async def search_fn(_query: str, _max_results: int) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TavilyTimeoutError(1.0)
        return {
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.org/a",
                    "content": "Snippet",
                }
            ]
        }

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("nexus.tools.web_search.asyncio.sleep", sleep)

    results = await WebSearch(_settings(ai_max_retries=1), search_fn=search_fn).search(
        "openai models",
    )

    assert calls["n"] == 2
    assert sleeps == [0.2]
    assert results[0].url == "https://example.org/a"


async def test_connection_error_is_retried_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    async def search_fn(_query: str, _max_results: int) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return {
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.org/a",
                    "content": "Snippet",
                }
            ]
        }

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("nexus.tools.web_search.asyncio.sleep", sleep)

    results = await WebSearch(_settings(ai_max_retries=1), search_fn=search_fn).search(
        "openai models",
    )

    assert calls["n"] == 2
    assert sleeps == [0.2]
    assert results[0].url == "https://example.org/a"


async def test_bad_request_is_not_retried() -> None:
    calls = {"n": 0}

    async def search_fn(_query: str, _max_results: int) -> object:
        calls["n"] += 1
        raise BadRequestError("bad query secret")

    with pytest.raises(ResearchError) as exc_info:
        await WebSearch(_settings(ai_max_retries=2), search_fn=search_fn).search(
            "openai models",
        )

    assert calls["n"] == 1
    assert exc_info.value.code == "provider_unavailable"
    assert "bad query secret" not in exc_info.value.message


async def test_invalid_key_is_not_retried() -> None:
    calls = {"n": 0}

    async def search_fn(_query: str, _max_results: int) -> object:
        calls["n"] += 1
        raise InvalidAPIKeyError("tvly-secret-value")

    with pytest.raises(ResearchError) as exc_info:
        await WebSearch(_settings(ai_max_retries=2), search_fn=search_fn).search(
            "openai models",
        )

    error = exc_info.value
    assert calls["n"] == 1
    assert error.code == "provider_unavailable"
    assert "tvly-secret-value" not in error.message


async def test_rate_limit_is_not_retried() -> None:
    from tavily.errors import UsageLimitExceededError

    calls = {"n": 0}

    async def search_fn(_query: str, _max_results: int) -> object:
        calls["n"] += 1
        raise UsageLimitExceededError("quota secret")

    with pytest.raises(ResearchError) as exc_info:
        await WebSearch(_settings(ai_max_retries=1), search_fn=search_fn).search(
            "openai models",
        )

    assert calls["n"] == 1
    assert exc_info.value.code == "provider_rate_limited"
    assert "quota secret" not in exc_info.value.message
