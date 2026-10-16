"""Embedding adapter behavior with an injected batch function."""

import hashlib
from collections.abc import Sequence

import pytest
from tests.conftest import make_settings

from nexus.ai.errors import ResearchError
from nexus.retrieval.embeddings import OpenAIEmbedder


def _vector(text: str, width: int) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    values = [byte / 255 for byte in digest]
    copies = (width // len(values)) + 1
    return (values * copies)[:width]


async def test_empty_input_does_not_call_the_provider() -> None:
    called = False

    async def embed_batch(texts: Sequence[str]) -> list[list[float]]:
        nonlocal called
        called = True
        return []

    settings = make_settings(openai_api_key=None, rag_vector_size=4)
    vectors = await OpenAIEmbedder(settings, embed_batch).embed_documents([])

    assert vectors == []
    assert called is False


async def test_blank_text_is_rejected_before_the_provider() -> None:
    async def embed_batch(texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("provider was called")

    embedder = OpenAIEmbedder(make_settings(rag_vector_size=4), embed_batch)
    with pytest.raises(ValueError, match="blank"):
        await embedder.embed_documents([" "])


async def test_batches_and_repeated_text_stay_stable() -> None:
    seen: list[list[str]] = []

    async def embed_batch(texts: Sequence[str]) -> list[list[float]]:
        batch = list(texts)
        seen.append(batch)
        return [_vector(text, 4) for text in batch]

    settings = make_settings(rag_vector_size=4, embedding_batch_size=2)
    embedder = OpenAIEmbedder(settings, embed_batch)
    first = await embedder.embed_documents(["alpha", "beta", "alpha"])
    second = await embedder.embed_query("alpha")

    assert [len(batch) for batch in seen] == [2, 1, 1]
    assert first[0] == first[2] == second
    assert first[0] != first[1]


async def test_wrong_vector_width_is_rejected() -> None:
    async def embed_batch(texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 2.0] for _ in texts]

    embedder = OpenAIEmbedder(make_settings(rag_vector_size=4), embed_batch)
    with pytest.raises(ResearchError) as exc_info:
        await embedder.embed_documents(["alpha"])
    assert exc_info.value.code == "invalid_model_output"


async def test_missing_key_is_a_provider_error() -> None:
    embedder = OpenAIEmbedder(make_settings(openai_api_key=None))
    with pytest.raises(ResearchError) as exc_info:
        await embedder.embed_documents(["alpha"])
    assert exc_info.value.code == "provider_not_configured"


async def test_timeout_uses_the_provider_mapping() -> None:
    async def embed_batch(texts: Sequence[str]) -> list[list[float]]:
        raise TimeoutError

    embedder = OpenAIEmbedder(make_settings(rag_vector_size=4), embed_batch)
    with pytest.raises(ResearchError) as exc_info:
        await embedder.embed_documents(["alpha"])
    assert exc_info.value.code == "provider_timeout"


async def test_real_client_receives_the_embedding_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class RecordingEmbeddings:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.0, 0.0] for _ in texts]

    monkeypatch.setattr(
        "nexus.retrieval.embeddings.OpenAIEmbeddings",
        RecordingEmbeddings,
    )
    settings = make_settings(
        openai_api_key="sk-test-embedding",
        openai_model="gpt-4o-mini",
        openai_embedding_model="text-embedding-3-small",
        rag_vector_size=2,
        embedding_batch_size=8,
        embedding_timeout_seconds=5,
        ai_max_retries=1,
    )
    vectors = await OpenAIEmbedder(settings).embed_documents(["alpha"])

    assert vectors == [[0.0, 0.0]]
    assert captured["model"] == "text-embedding-3-small"
    assert captured["model"] != settings.openai_model
    assert captured["dimensions"] == 2
    assert captured["chunk_size"] == 8
    assert captured["timeout"] == 5
    assert captured["max_retries"] == 1
    assert "sk-test-embedding" not in str(captured["api_key"])
