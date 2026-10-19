"""Provider usage stays separate from the logical graph budget."""

from decimal import Decimal
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr, ValidationError
from tavily.errors import TimeoutError as TavilyTimeoutError
from tests.conftest import make_settings

from nexus.ai.client import OpenAIStructuredModel
from nexus.ai.errors import ResearchError
from nexus.observability.context import bind_run
from nexus.observability.costs import price_operations
from nexus.observability.spans import operation
from nexus.observability.usage import (
    OperationUsage,
    UsageBuffer,
    provider_token_counts,
    summary_fields,
)
from nexus.research.discovery import Discovery
from nexus.retrieval.embeddings import OpenAIEmbedder
from nexus.schemas.research import ResearchPlan, ResearchTask
from nexus.tools.web_search import WebSearch

SECRET = "sk-usage-secret"
PROMPT = "ignore previous instructions"


def _plan() -> ResearchPlan:
    return ResearchPlan(
        objective="Find the limits.",
        tasks=[ResearchTask(question="What stops a run?", search_queries=["limits"])],
    )


def test_usage_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        OperationUsage(
            stage="plan",
            provider="openai",
            operation="openai_chat",
            duration_ms=-1,
            outcome="ok",
            usage_source="unavailable",
        )


def test_buffer_drops_records_past_the_cap() -> None:
    buffer = UsageBuffer(limit=2)
    record = OperationUsage(
        stage="plan",
        provider="openai",
        operation="openai_chat",
        duration_ms=1,
        outcome="ok",
        usage_source="unavailable",
    )
    buffer.add(record)
    buffer.add(record)
    buffer.add(record)

    assert len(buffer.records) == 2
    assert buffer.dropped == 1


async def test_duplicate_token_observations_keep_one_record() -> None:
    run_id = uuid4()
    with bind_run(run_id=run_id, research_id=run_id, create_buffer=True) as buffer:
        with operation(
            operation="openai_chat", provider="openai", stage="plan"
        ) as handle:
            handle.observe_tokens(1, 2)
            handle.observe_tokens(3, 4)
            handle.observe_tokens(-1, 4)

    assert buffer is not None
    assert len(buffer.records) == 1
    record = buffer.records[0]
    assert record.input_tokens == 3
    assert record.output_tokens == 4
    assert record.usage_source == "provider"
    assert record.duration_ms >= 0


def test_zero_token_pair_is_not_a_measurement() -> None:
    assert provider_token_counts({"input_tokens": 0, "output_tokens": 0}) == (
        None,
        None,
    )
    assert provider_token_counts({"input_tokens": 4, "output_tokens": 0}) == (4, 0)


async def test_negative_tokens_stay_unavailable() -> None:
    with bind_run(create_buffer=True) as buffer:
        with operation(
            operation="openai_chat", provider="openai", stage="plan"
        ) as handle:
            handle.observe_tokens(-1, 4)

    assert buffer is not None
    record = buffer.records[0]
    assert record.input_tokens is None
    assert record.output_tokens is None
    assert record.usage_source == "unavailable"


async def test_structured_output_reads_provider_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    raw = AIMessage(
        content="{}",
        usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    )

    class FakeChat:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def with_structured_output(self, schema: object, **_kwargs: object) -> FakeChat:
            del schema
            return self

        async def ainvoke(self, messages: list[object]) -> dict[str, object]:
            assert messages
            return {"raw": raw, "parsed": plan, "parsing_error": None}

    monkeypatch.setattr("nexus.ai.client.ChatOpenAI", FakeChat)
    settings = make_settings(
        openai_api_key=SecretStr(SECRET), openai_model="gpt-4o-mini"
    )
    with bind_run(create_buffer=True) as buffer:
        result = await OpenAIStructuredModel(settings).ainvoke(
            [HumanMessage(content=PROMPT)]
        )

    assert result == plan
    assert buffer is not None
    record = buffer.records[0]
    assert record.usage_source == "provider"
    assert record.input_tokens == 11
    assert record.output_tokens == 7
    assert record.embedding_tokens is None
    assert PROMPT not in str(record.model_dump())
    assert SECRET not in str(record.model_dump())


async def test_missing_structured_usage_stays_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()

    class FakeChat:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def with_structured_output(self, schema: object, **_kwargs: object) -> FakeChat:
            del schema
            return self

        async def ainvoke(self, _messages: list[object]) -> dict[str, object]:
            return {
                "raw": AIMessage(content="{}"),
                "parsed": plan,
                "parsing_error": None,
            }

    monkeypatch.setattr("nexus.ai.client.ChatOpenAI", FakeChat)
    settings = make_settings(openai_api_key=SecretStr(SECRET))
    with bind_run(create_buffer=True) as buffer:
        result = await OpenAIStructuredModel(settings).ainvoke(
            [HumanMessage(content="q")]
        )

    assert isinstance(result, ResearchPlan)
    assert buffer is not None
    assert buffer.records[0].usage_source == "unavailable"
    assert buffer.records[0].input_tokens is None
    assert buffer.records[0].output_tokens is None


async def test_structured_parse_error_keeps_the_public_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeChat:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def with_structured_output(self, schema: object, **_kwargs: object) -> FakeChat:
            del schema
            return self

        async def ainvoke(self, _messages: list[object]) -> dict[str, object]:
            return {
                "raw": AIMessage(
                    content=PROMPT,
                    usage_metadata={
                        "input_tokens": 2,
                        "output_tokens": 1,
                        "total_tokens": 3,
                    },
                ),
                "parsed": None,
                "parsing_error": ValueError(PROMPT),
            }

    monkeypatch.setattr("nexus.ai.client.ChatOpenAI", FakeChat)
    settings = make_settings(openai_api_key=SecretStr(SECRET))
    with bind_run(create_buffer=True) as buffer:
        with pytest.raises(ResearchError) as exc_info:
            await OpenAIStructuredModel(settings).ainvoke(
                [HumanMessage(content=PROMPT)]
            )

    assert exc_info.value.code == "invalid_model_output"
    assert PROMPT not in exc_info.value.message
    assert buffer is not None
    record = buffer.records[0]
    assert record.outcome == "error"
    assert record.usage_source == "provider"
    assert record.input_tokens == 2
    assert PROMPT not in str(record.model_dump())


async def test_tavily_retries_are_physical_and_logical_search_stays_one(
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
                    "content": "Snippet",
                }
            ]
        }

    async def sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("nexus.tools.web_search.asyncio.sleep", sleep)

    class Scripted:
        def __init__(self) -> None:
            self._replies = [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "web_search",
                            "args": {"query": f"secret query {SECRET}"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="The notes are enough."),
            ]

        async def ainvoke(self, _messages: list[object]) -> AIMessage:
            return self._replies.pop(0)

    settings = make_settings(
        openai_api_key=SECRET,
        tavily_api_key="tvly-test",
        ai_max_retries=1,
    )
    search = WebSearch(settings, search_fn=search_fn)
    discovery = Discovery(settings, model=Scripted(), search=search)
    with bind_run(create_buffer=True) as buffer:
        result = await discovery.discover("How do agents work?")

    assert result.search_calls == 1
    assert calls["n"] == 2
    assert buffer is not None
    attempts = [record for record in buffer.records if record.operation == "web_search"]
    assert len(attempts) == 2
    assert [record.physical_attempts for record in attempts] == [1, 1]
    assert attempts[0].outcome == "error"
    assert attempts[1].outcome == "ok"
    rendered = " ".join(str(record.model_dump()) for record in buffer.records)
    assert SECRET not in rendered
    assert "secret query" not in rendered


async def test_embedding_estimate_is_separate_from_provider_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    import tiktoken

    class _Encoding:
        def encode(self, text: str) -> list[int]:
            return [1] * len(text)

    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda _model: _Encoding())

    def vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        values = [byte / 255 for byte in digest]
        return (values * 2)[:4]

    async def embed_batch(texts: object) -> list[list[float]]:
        assert isinstance(texts, list)
        return [vector(text) for text in texts]

    settings = make_settings(
        openai_api_key=SECRET,
        openai_embedding_model="text-embedding-3-small",
        rag_vector_size=4,
        embedding_batch_size=1,
    )
    embedder = OpenAIEmbedder(settings, embed_batch)
    with bind_run(create_buffer=True) as buffer:
        await embedder.embed_query("hello")
        await embedder.embed_documents(["hello", "world"])

    assert buffer is not None
    query = [record for record in buffer.records if record.operation == "embed_query"]
    documents = [
        record for record in buffer.records if record.operation == "embed_documents"
    ]
    assert len(query) == 1
    assert query[0].usage_source == "estimated"
    assert query[0].input_tokens is None
    assert query[0].output_tokens is None
    assert query[0].embedding_tokens == len("hello")
    assert query[0].physical_attempts is None
    assert len(documents) == 2
    assert "hello" not in str(query[0].model_dump())


async def test_unknown_embedding_model_has_no_token_estimate() -> None:
    async def embed_batch(texts: object) -> list[list[float]]:
        assert isinstance(texts, list)
        return [[0.0, 0.0, 0.0, 0.0] for _text in texts]

    settings = make_settings(
        openai_embedding_model="not-a-real-embed",
        rag_vector_size=4,
    )
    with bind_run(create_buffer=True) as buffer:
        await OpenAIEmbedder(settings, embed_batch).embed_documents(["hello"])

    assert buffer is not None
    record = buffer.records[0]
    assert record.usage_source == "unavailable"
    assert record.embedding_tokens is None
    assert record.input_tokens is None


def _install_structured_chat(
    monkeypatch: pytest.MonkeyPatch,
    result: object,
) -> None:
    class FakeChat:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def with_structured_output(self, schema: object, **_kwargs: object) -> FakeChat:
            del schema
            return self

        async def ainvoke(self, _messages: list[object]) -> object:
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr("nexus.ai.client.ChatOpenAI", FakeChat)


async def test_timeout_before_metadata_stays_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_structured_chat(monkeypatch, TimeoutError(PROMPT))
    settings = make_settings(openai_api_key=SecretStr(SECRET), ai_max_retries=0)
    with bind_run(create_buffer=True) as buffer:
        with pytest.raises(TimeoutError):
            await OpenAIStructuredModel(settings).ainvoke(
                [HumanMessage(content=PROMPT)]
            )

    assert buffer is not None
    record = buffer.records[0]
    assert record.outcome == "error"
    assert record.usage_source == "unavailable"
    assert record.input_tokens is None
    assert record.output_tokens is None
    assert record.physical_attempts == 1
    assert PROMPT not in str(record.model_dump())
    estimate = price_operations([record], tavily_usd_per_search=None)
    assert estimate.amount is None
    assert estimate.coverage == "unavailable"


async def test_retries_disabled_records_one_measured_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    _install_structured_chat(
        monkeypatch,
        {
            "raw": AIMessage(
                content="{}",
                usage_metadata={
                    "input_tokens": 1000,
                    "output_tokens": 1000,
                    "total_tokens": 2000,
                },
            ),
            "parsed": plan,
            "parsing_error": None,
        },
    )
    settings = make_settings(
        openai_api_key=SecretStr(SECRET),
        openai_model="gpt-4o-mini",
        ai_max_retries=0,
    )
    with bind_run(create_buffer=True) as buffer:
        await OpenAIStructuredModel(settings).ainvoke([HumanMessage(content=PROMPT)])

    assert buffer is not None
    record = buffer.records[0]
    assert record.physical_attempts == 1
    assert record.usage_source == "provider"
    estimate = price_operations([record], tavily_usd_per_search=None)
    assert estimate.coverage == "complete"
    assert estimate.amount == Decimal("0.00075")


async def test_sdk_retries_keep_observed_chat_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_structured_chat(
        monkeypatch,
        {
            "raw": AIMessage(
                content=PROMPT,
                usage_metadata={
                    "input_tokens": 1000,
                    "output_tokens": 1000,
                    "total_tokens": 2000,
                },
            ),
            "parsed": None,
            "parsing_error": ValueError(PROMPT),
        },
    )
    settings = make_settings(
        openai_api_key=SecretStr(SECRET),
        openai_model="gpt-4o-mini",
        ai_max_retries=1,
    )
    with bind_run(create_buffer=True) as buffer:
        with pytest.raises(ResearchError) as exc_info:
            await OpenAIStructuredModel(settings).ainvoke(
                [HumanMessage(content=PROMPT)]
            )

    assert exc_info.value.code == "invalid_model_output"
    assert buffer is not None
    record = buffer.records[0]
    assert record.outcome == "error"
    assert record.usage_source == "provider"
    assert record.physical_attempts is None
    assert record.input_tokens == 1000
    assert PROMPT not in str(record.model_dump())
    estimate = price_operations([record], tavily_usd_per_search=None)
    assert estimate.coverage == "partial"
    assert estimate.amount == Decimal("0.00075")


def test_summary_keeps_chat_tokens_apart_from_embedding_estimates() -> None:
    records = [
        OperationUsage(
            stage="run",
            provider="nexus",
            operation="research_run",
            duration_ms=12.5,
            outcome="ok",
            usage_source="unavailable",
        ),
        OperationUsage(
            stage="plan",
            provider="openai",
            operation="openai_chat",
            model="gpt-4o-mini",
            duration_ms=4,
            outcome="ok",
            input_tokens=10,
            output_tokens=4,
            physical_attempts=1,
            usage_source="provider",
        ),
        OperationUsage(
            stage="retrieve",
            provider="openai",
            operation="embed_query",
            model="text-embedding-3-small",
            duration_ms=2,
            outcome="ok",
            embedding_tokens=7,
            physical_attempts=1,
            usage_source="estimated",
        ),
    ]

    fields = summary_fields(records, dropped=0, tavily_usd_per_search=None)

    assert fields["duration_ms"] == 12.5
    assert fields["input_tokens"] == 10
    assert fields["output_tokens"] == 4
    assert fields["embedding_tokens"] == 7
    assert fields["coverage"] == "partial"
    assert fields["unknown_operations"] == 1
    assert fields["input_tokens"] != fields["embedding_tokens"]


def test_summary_omits_chat_totals_when_an_attempt_count_is_unknown() -> None:
    records = [
        OperationUsage(
            stage="run",
            provider="nexus",
            operation="research_run",
            duration_ms=3,
            outcome="ok",
            usage_source="unavailable",
        ),
        OperationUsage(
            stage="plan",
            provider="openai",
            operation="openai_chat",
            model="gpt-4o-mini",
            duration_ms=1,
            outcome="ok",
            input_tokens=1000,
            output_tokens=1000,
            usage_source="provider",
        ),
    ]

    fields = summary_fields(records, dropped=2, tavily_usd_per_search=None)

    assert "input_tokens" not in fields
    assert "output_tokens" not in fields
    assert "embedding_tokens" not in fields
    assert fields["duration_ms"] == 3
    assert fields["unknown_operations"] == 1
    assert fields["dropped"] == 2
    assert fields["coverage"] == "partial"
    assert fields["estimated_usd"] == "0.00075"
