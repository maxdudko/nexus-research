"""Provider usage stays separate from the logical graph budget."""

from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr, ValidationError
from tavily.errors import TimeoutError as TavilyTimeoutError
from tests.conftest import make_settings

from nexus.ai.client import OpenAIStructuredModel
from nexus.ai.errors import ResearchError
from nexus.observability.context import bind_run
from nexus.observability.spans import operation
from nexus.observability.usage import OperationUsage, UsageBuffer
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


async def test_embedding_estimate_is_separate_from_provider_tokens() -> None:
    import hashlib

    import tiktoken

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
    expected = len(
        tiktoken.encoding_for_model("text-embedding-3-small").encode("hello")
    )
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
    assert query[0].embedding_tokens == expected
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
