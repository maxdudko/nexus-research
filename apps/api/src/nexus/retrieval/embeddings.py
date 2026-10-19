"""Async OpenAI embeddings. Tests inject the batch call and make no network request."""

import math
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from langchain_openai import OpenAIEmbeddings

from nexus.ai.errors import (
    ResearchError,
    invalid_model_output,
    map_provider_exception,
    provider_not_configured,
)
from nexus.core.config import Settings
from nexus.observability.spans import operation as operation_span
from nexus.observability.usage import estimate_text_tokens, sdk_attempt_count

EmbedBatch = Callable[[Sequence[str]], Awaitable[Sequence[Sequence[float]]]]


class Embedder(Protocol):
    """The embedding calls Group B and Group C depend on."""

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    """Embed with the configured model. The chat model is not used."""

    def __init__(
        self,
        settings: Settings,
        embed_batch: EmbedBatch | None = None,
    ) -> None:
        self._settings = settings
        self._embed_batch = embed_batch

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts in bounded batches. An empty sequence does not build a client."""
        if not texts:
            return []
        return await self._embed(texts, operation="embed_documents")

    async def embed_query(self, text: str) -> list[float]:
        """Embed one query string with the same model and dimension as documents."""
        vectors = await self._embed([text], operation="embed_query")
        return vectors[0]

    async def _embed(
        self,
        texts: Sequence[str],
        *,
        operation: str,
    ) -> list[list[float]]:
        _require_texts(texts)
        embedder = self._client()
        vectors: list[list[float]] = []
        width = self._settings.rag_vector_size
        for batch in _batches(list(texts), self._settings.embedding_batch_size):
            vectors.extend(await self._embed_one(batch, embedder, operation=operation))
        wrong_count = len(vectors) != len(texts)
        wrong_width = any(len(vector) != width for vector in vectors)
        if wrong_count or wrong_width:
            raise invalid_model_output()
        return vectors

    def _client(self) -> OpenAIEmbeddings | None:
        if self._embed_batch is not None:
            return None
        key = self._settings.openai_api_key
        if key is None:
            raise provider_not_configured()
        # Pass the key explicitly so an ambient OPENAI_API_KEY is not used.
        return OpenAIEmbeddings(
            model=self._settings.openai_embedding_model,
            api_key=key,
            timeout=self._settings.embedding_timeout_seconds,
            max_retries=self._settings.ai_max_retries,
            dimensions=self._settings.rag_vector_size,
            chunk_size=self._settings.embedding_batch_size,
        )

    async def _embed_one(
        self,
        batch: list[str],
        embedder: OpenAIEmbeddings | None,
        *,
        operation: str,
    ) -> list[list[float]]:
        model = self._settings.openai_embedding_model
        with operation_span(
            operation=operation,
            provider="openai",
            model=model,
        ) as handle:
            handle.physical_attempts = sdk_attempt_count(self._settings.ai_max_retries)
            embed_batch = self._embed_batch
            try:
                if embed_batch is not None:
                    raw = await embed_batch(batch)
                else:
                    if embedder is None:
                        raise provider_not_configured()
                    raw = await embedder.aembed_documents(batch)
            except ResearchError:
                raise
            except Exception as exc:
                raise map_provider_exception(exc) from None
            try:
                vectors = [_finite_vector(vector) for vector in raw]
            except ResearchError:
                raise
            except TypeError, ValueError:
                raise invalid_model_output() from None
            handle.observe_embedding(estimate_text_tokens(batch, model))
            return vectors


def _finite_vector(vector: object) -> list[float]:
    """Reject a provider vector that is not a finite sequence of numbers."""
    if isinstance(vector, str) or not isinstance(vector, Sequence):
        raise invalid_model_output()
    values: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise invalid_model_output()
        number = float(value)
        if not math.isfinite(number):
            raise invalid_model_output()
        values.append(number)
    return values


def _require_texts(texts: Sequence[str]) -> None:
    if isinstance(texts, str):
        raise TypeError("texts must be a sequence of strings")
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must not be blank")


def _batches(texts: list[str], size: int) -> list[list[str]]:
    return [texts[index : index + size] for index in range(0, len(texts), size)]
