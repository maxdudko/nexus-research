"""Async OpenAI embeddings. Tests inject the batch call and make no network request."""

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
        _require_texts(texts)
        embedder = self._client()
        vectors: list[list[float]] = []
        width = self._settings.rag_vector_size
        for batch in _batches(list(texts), self._settings.embedding_batch_size):
            vectors.extend(await self._embed_one(batch, embedder))
        wrong_count = len(vectors) != len(texts)
        wrong_width = any(len(vector) != width for vector in vectors)
        if wrong_count or wrong_width:
            raise invalid_model_output()
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Embed one query string with the same model and dimension as documents."""
        vectors = await self.embed_documents([text])
        return vectors[0]

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
    ) -> list[list[float]]:
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
            return [[float(value) for value in vector] for vector in raw]
        except TypeError, ValueError:
            raise invalid_model_output() from None


def _require_texts(texts: Sequence[str]) -> None:
    if isinstance(texts, str):
        raise TypeError("texts must be a sequence of strings")
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must not be blank")


def _batches(texts: list[str], size: int) -> list[list[str]]:
    return [texts[index : index + size] for index in range(0, len(texts), size)]
