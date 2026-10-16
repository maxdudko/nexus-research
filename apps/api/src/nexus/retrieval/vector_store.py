"""Qdrant collection contract for one versioned evidence index.

The adapter never deletes or recreates a collection. A size or distance
mismatch raises ``collection_mismatch`` and leaves the existing collection in
place. Every search filter includes ``research_id`` on the server.
"""

import logging
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from nexus.ai.errors import ResearchError, collection_mismatch, provider_unavailable
from nexus.core.config import Settings
from nexus.models.source import CONTENT_HASH
from nexus.retrieval.ids import evidence_point_id

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_SEARCH_LIMIT = 10
_PAYLOAD_FIELDS = ("research_id", "source_id")


@dataclass(frozen=True)
class EvidencePoint:
    """One passage to upsert. The point id is derived from these fields."""

    research_id: uuid.UUID
    source_id: uuid.UUID
    chunk_index: int
    text: str
    vector: Sequence[float]
    content_hash: str
    page_number: int | None = None
    url: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class EvidenceMatch:
    """A scored passage. The score is cosine similarity, not confidence."""

    chunk_id: uuid.UUID
    source_id: uuid.UUID
    research_id: uuid.UUID
    score: float
    text: str
    page_number: int | None
    url: str | None
    title: str | None
    content_hash: str
    chunk_index: int


def create_qdrant_client(settings: Settings) -> AsyncQdrantClient:
    """Open an HTTP client. gRPC stays off; readiness does not use this client."""
    timeout_seconds = max(1, math.ceil(settings.embedding_timeout_seconds))
    return AsyncQdrantClient(
        url=str(settings.qdrant_url),
        prefer_grpc=False,
        timeout=timeout_seconds,
    )


class EvidenceVectorStore:
    """Upsert, delete, and search one collection named by settings."""

    def __init__(
        self,
        settings: Settings,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client if client is not None else create_qdrant_client(settings)
        self._owns_client = client is None

    async def close(self) -> None:
        """Close a client this store constructed. An injected client stays open."""
        if self._owns_client:
            await self._client.close()

    async def ensure_collection(self) -> None:
        """Create a compatible collection, or refuse one that does not match."""
        try:
            await self._ensure_collection()
        except ResearchError:
            raise
        except Exception as exc:
            _log_failure("qdrant collection check failed", exc)
            raise provider_unavailable() from None

    async def upsert(
        self,
        *,
        research_id: uuid.UUID,
        points: Sequence[EvidencePoint],
    ) -> None:
        """Overwrite stable point ids. Every point must use ``research_id``."""
        width = self._settings.rag_vector_size
        structs = [_point_struct(point, research_id, width) for point in points]
        if not structs:
            return
        try:
            await self._ensure_collection()
            await self._client.upsert(
                collection_name=self._settings.rag_collection,
                points=structs,
                wait=True,
            )
        except ResearchError:
            raise
        except Exception as exc:
            _log_failure("qdrant upsert failed", exc)
            raise provider_unavailable() from None

    async def delete_hash(
        self,
        *,
        research_id: uuid.UUID,
        source_id: uuid.UUID,
        content_hash: str,
    ) -> None:
        """Delete one attempt's points after a newer claim has taken the token."""
        digest = _require_hash(content_hash)
        await self._delete(
            _source_filter(research_id, source_id, content_hash=digest),
        )

    async def delete_except_hash(
        self,
        *,
        research_id: uuid.UUID,
        source_id: uuid.UUID,
        content_hash: str,
    ) -> None:
        """Delete this source's points whose digest is not the accepted one."""
        digest = _require_hash(content_hash)
        await self._delete(
            _source_filter(research_id, source_id, excluded_hash=digest),
        )

    async def search(
        self,
        *,
        research_id: uuid.UUID,
        vector: Sequence[float],
        limit: int,
        content_hash: str | None = None,
    ) -> list[EvidenceMatch]:
        """Search with a server-side research filter. ``research_id`` is required."""
        if not 1 <= limit <= _SEARCH_LIMIT:
            raise ValueError("limit must be from 1 to 10")
        if len(vector) != self._settings.rag_vector_size:
            raise ValueError("vector length must match the configured dimension")
        if content_hash is not None:
            content_hash = _require_hash(content_hash)
        query_filter = _research_filter(research_id, content_hash)
        try:
            await self._ensure_collection()
            response = await self._client.query_points(
                collection_name=self._settings.rag_collection,
                query=[float(value) for value in vector],
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        except ResearchError:
            raise
        except Exception as exc:
            _log_failure("qdrant search failed", exc)
            raise provider_unavailable() from None
        matches: list[EvidenceMatch] = []
        for point in response.points:
            match = _match_from_point(point.payload, point.score, research_id)
            if match is not None:
                matches.append(match)
        return matches

    async def _delete(self, points_filter: models.Filter) -> None:
        try:
            await self._ensure_collection()
            await self._client.delete(
                collection_name=self._settings.rag_collection,
                points_selector=models.FilterSelector(filter=points_filter),
                wait=True,
            )
        except ResearchError:
            raise
        except Exception as exc:
            _log_failure("qdrant delete failed", exc)
            raise provider_unavailable() from None

    async def _ensure_collection(self) -> None:
        name = self._settings.rag_collection
        if not await self._client.collection_exists(name):
            await self._create_collection(name)
        info = await self._client.get_collection(name)
        _require_vectors(info, self._settings.rag_vector_size)
        await self._ensure_payload_indexes(name, info)

    async def _create_collection(self, name: str) -> None:
        vectors = models.VectorParams(
            size=self._settings.rag_vector_size,
            distance=models.Distance.COSINE,
        )
        try:
            await self._client.create_collection(
                collection_name=name,
                vectors_config=vectors,
            )
        except UnexpectedResponse as exc:
            if not await self._client.collection_exists(name):
                _log_failure("qdrant create collection failed", exc)
                raise provider_unavailable() from None

    async def _ensure_payload_indexes(
        self,
        name: str,
        info: models.CollectionInfo,
    ) -> None:
        indexed = set(info.payload_schema)
        for field_name in _PAYLOAD_FIELDS:
            if field_name in indexed:
                continue
            try:
                await self._client.create_payload_index(
                    collection_name=name,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            except UnexpectedResponse as exc:
                current = await self._client.get_collection(name)
                if field_name not in current.payload_schema:
                    _log_failure("qdrant payload index failed", exc)
                    raise provider_unavailable() from None


def _research_filter(research_id: uuid.UUID, content_hash: str | None) -> models.Filter:
    must: list[models.Condition] = [_match_field("research_id", str(research_id))]
    if content_hash is not None:
        must.append(_match_field("content_hash", content_hash))
    return models.Filter(must=must)


def _source_filter(
    research_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    content_hash: str | None = None,
    excluded_hash: str | None = None,
) -> models.Filter:
    must: list[models.Condition] = [
        _match_field("research_id", str(research_id)),
        _match_field("source_id", str(source_id)),
    ]
    if content_hash is not None:
        must.append(_match_field("content_hash", content_hash))
    must_not: list[models.Condition] | None = None
    if excluded_hash is not None:
        must_not = [_match_field("content_hash", excluded_hash)]
    return models.Filter(must=must, must_not=must_not)


def _match_field(key: str, value: str) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _require_vectors(info: models.CollectionInfo, size: int) -> None:
    vectors = info.config.params.vectors
    if not isinstance(vectors, models.VectorParams):
        logger.warning("qdrant collection uses named or missing vectors")
        raise collection_mismatch()
    if vectors.size != size or vectors.distance != models.Distance.COSINE:
        logger.warning(
            "qdrant collection rejected: size=%s distance=%s",
            vectors.size,
            vectors.distance,
        )
        raise collection_mismatch()


def _point_struct(
    point: EvidencePoint,
    research_id: uuid.UUID,
    width: int,
) -> models.PointStruct:
    if point.research_id != research_id:
        raise ValueError("point research_id must match the upsert scope")
    if point.chunk_index < 0:
        raise ValueError("chunk_index must be non-negative")
    if not point.text.strip():
        raise ValueError("text must not be blank")
    if point.page_number is not None and point.page_number < 1:
        raise ValueError("page_number must be at least 1")
    digest = _require_hash(point.content_hash)
    if len(point.vector) != width or not _finite(point.vector):
        raise ValueError("vector length must match the configured dimension")
    chunk_id = evidence_point_id(point.source_id, digest, point.chunk_index)
    payload: dict[str, str | int | None] = {
        "research_id": str(point.research_id),
        "source_id": str(point.source_id),
        "chunk_id": str(chunk_id),
        "chunk_index": point.chunk_index,
        "text": point.text,
        "page_number": point.page_number,
        "url": point.url,
        "title": point.title,
        "content_hash": digest,
        "schema_version": SCHEMA_VERSION,
    }
    return models.PointStruct(
        id=str(chunk_id),
        vector=[float(value) for value in point.vector],
        payload=payload,
    )


def _finite(values: Sequence[float]) -> bool:
    return all(
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        for value in values
    )


def _require_hash(value: str) -> str:
    if CONTENT_HASH.fullmatch(value) is None:
        raise ValueError("content_hash must be 64 lowercase hex characters")
    return value


def _match_from_point(
    payload: models.Payload | None,
    score: float | None,
    research_id: uuid.UUID,
) -> EvidenceMatch | None:
    if not isinstance(payload, Mapping) or score is None:
        return None
    try:
        parsed_research = uuid.UUID(str(payload["research_id"]))
        if parsed_research != research_id:
            return None
        if payload.get("schema_version") != SCHEMA_VERSION:
            return None
        text = payload["text"]
        digest = payload["content_hash"]
        if not isinstance(text, str) or not text.strip():
            return None
        if not isinstance(digest, str):
            return None
        return EvidenceMatch(
            chunk_id=uuid.UUID(str(payload["chunk_id"])),
            source_id=uuid.UUID(str(payload["source_id"])),
            research_id=parsed_research,
            score=float(score),
            text=text,
            page_number=_page_number(payload.get("page_number")),
            url=_optional_str(payload.get("url")),
            title=_optional_str(payload.get("title")),
            content_hash=digest,
            chunk_index=_chunk_index(payload["chunk_index"]),
        )
    except KeyError, TypeError, ValueError:
        return None


def _page_number(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("page_number")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("text field")
    return value


def _chunk_index(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("chunk_index")
    return value


def _log_failure(message: str, exc: Exception) -> None:
    logger.warning("%s: %s", message, type(exc).__name__)
