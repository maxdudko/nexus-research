"""Qdrant collection contract for one versioned evidence index.

The adapter never deletes or recreates a collection. A size, distance, or
payload-index mismatch raises ``collection_mismatch`` and leaves the existing
collection in place. Every search filter includes ``research_id`` on the server.

Each point stores the ``claim_token`` that wrote it. The point id includes
that token, so two attempts do not share ids. Upsert stores ``accepted`` as
false. ``retire_other_claims`` sets it true while the row lock is held.
Other accepted tokens are deleted only after PostgreSQL commits that version.
``delete_claim`` removes only its own token, accepted or not. ``search`` can
see every stored digest. Group C calls ``search_accepted`` with
``accepted_versions``; that filter also requires ``accepted`` true.
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
from nexus.observability.spans import operation
from nexus.retrieval.ids import evidence_point_id
from nexus.retrieval.versions import AcceptedVersion

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SEARCH_LIMIT = 10
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
    claim_token: uuid.UUID
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
        with operation(operation="qdrant_upsert", provider="qdrant"):
            await self._upsert(research_id, points)

    async def _upsert(
        self,
        research_id: uuid.UUID,
        points: Sequence[EvidencePoint],
    ) -> None:
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

    async def delete_claim(
        self,
        *,
        research_id: uuid.UUID,
        source_id: uuid.UUID,
        claim_token: uuid.UUID,
    ) -> None:
        """Delete points tagged with this claim token.

        Points tagged with another token stay in the collection.
        """
        await self._delete(
            _claim_filter(research_id, source_id, claim_token, exclude=False),
        )

    async def delete_other_claims(
        self,
        *,
        research_id: uuid.UUID,
        source_id: uuid.UUID,
        claim_token: uuid.UUID,
    ) -> None:
        """Delete this source's accepted points written by another token.

        Unaccepted points stay, including a claim that started after publish.
        ``delete_claim`` still removes one token whether or not it is accepted.
        """
        await self._delete(
            _claim_filter(research_id, source_id, claim_token, exclude=True),
        )

    async def accept_claim(
        self,
        *,
        research_id: uuid.UUID,
        source_id: uuid.UUID,
        claim_token: uuid.UUID,
    ) -> None:
        """Mark this token's points accepted. Other tokens stay unchanged."""
        try:
            await self._ensure_collection()
            await self._client.set_payload(
                collection_name=self._settings.rag_collection,
                payload={"accepted": True},
                points=_claim_filter(
                    research_id,
                    source_id,
                    claim_token,
                    exclude=False,
                ),
                wait=True,
            )
        except ResearchError:
            raise
        except Exception as exc:
            _log_failure("qdrant accept failed", exc)
            raise provider_unavailable() from None

    async def search(
        self,
        *,
        research_id: uuid.UUID,
        vector: Sequence[float],
        limit: int,
        content_hash: str | None = None,
    ) -> list[EvidenceMatch]:
        """Search with a server-side research filter.

        The result can include a digest that a newer attempt has replaced.
        Group C calls ``search_accepted`` with ``accepted_versions(...)``.
        """
        if content_hash is not None:
            content_hash = _require_hash(content_hash)
        query = _require_query(vector, limit, self._settings.rag_vector_size)
        return await self._query(
            research_id,
            query,
            limit,
            _research_filter(research_id, content_hash),
        )

    async def search_accepted(
        self,
        *,
        research_id: uuid.UUID,
        vector: Sequence[float],
        limit: int,
        versions: Sequence[AcceptedVersion],
    ) -> list[EvidenceMatch]:
        """Search the accepted digest of each source.

        An empty version list returns no matches and does not query Qdrant.
        Points whose payload ``accepted`` flag is false are omitted.
        """
        if not versions:
            return []
        query = _require_query(vector, limit, self._settings.rag_vector_size)
        return await self._query(
            research_id,
            query,
            limit,
            _accepted_filter(research_id, versions),
            accepted_only=True,
        )

    async def _query(
        self,
        research_id: uuid.UUID,
        vector: list[float],
        limit: int,
        query_filter: models.Filter,
        *,
        accepted_only: bool = False,
    ) -> list[EvidenceMatch]:
        with operation(operation="qdrant_search", provider="qdrant"):
            return await self._search(
                research_id,
                vector,
                limit,
                query_filter,
                accepted_only,
            )

    async def _search(
        self,
        research_id: uuid.UUID,
        vector: list[float],
        limit: int,
        query_filter: models.Filter,
        accepted_only: bool,
    ) -> list[EvidenceMatch]:
        try:
            await self._ensure_collection()
            response = await self._client.query_points(
                collection_name=self._settings.rag_collection,
                query=vector,
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
            match = _match_from_point(
                point.payload,
                point.score,
                research_id,
                accepted_only=accepted_only,
            )
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
        for field_name in _PAYLOAD_FIELDS:
            if _keyword_index(info, field_name):
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
                if _keyword_index(current, field_name):
                    continue
                _log_failure("qdrant payload index failed", exc)
                raise provider_unavailable() from None


def _research_filter(research_id: uuid.UUID, content_hash: str | None) -> models.Filter:
    must: list[models.Condition] = [_match_field("research_id", str(research_id))]
    if content_hash is not None:
        must.append(_match_field("content_hash", content_hash))
    return models.Filter(must=must)


def _claim_filter(
    research_id: uuid.UUID,
    source_id: uuid.UUID,
    claim_token: uuid.UUID,
    *,
    exclude: bool,
) -> models.Filter:
    must: list[models.Condition] = [
        _match_field("research_id", str(research_id)),
        _match_field("source_id", str(source_id)),
    ]
    token = _match_field("claim_token", str(claim_token))
    if exclude:
        must.append(_match_bool("accepted", True))
        return models.Filter(must=must, must_not=[token])
    must.append(token)
    return models.Filter(must=must)


def _accepted_filter(
    research_id: uuid.UUID,
    versions: Sequence[AcceptedVersion],
) -> models.Filter:
    should: list[models.Condition] = []
    for version in versions:
        digest = _require_hash(version.content_hash)
        pair: list[models.Condition] = [
            _match_field("source_id", str(version.source_id)),
            _match_field("content_hash", digest),
        ]
        should.append(models.Filter(must=pair))
    must: list[models.Condition] = [
        _match_field("research_id", str(research_id)),
        _match_bool("accepted", True),
    ]
    return models.Filter(
        must=must,
        should=should,
        min_should=models.MinShould(conditions=should, min_count=1),
    )


def _require_query(vector: Sequence[float], limit: int, width: int) -> list[float]:
    if not 1 <= limit <= SEARCH_LIMIT:
        raise ValueError("limit must be from 1 to 10")
    if len(vector) != width or not _finite(vector):
        raise ValueError("vector length must match the configured dimension")
    return [float(value) for value in vector]


def _keyword_index(info: models.CollectionInfo, field_name: str) -> bool:
    data_type = _payload_index_type(info, field_name)
    if data_type is None:
        return False
    if data_type is models.PayloadSchemaType.KEYWORD:
        return True
    _reject_payload_index(info, field_name)
    return False


def _payload_index_type(
    info: models.CollectionInfo,
    field_name: str,
) -> models.PayloadSchemaType | None:
    if field_name not in info.payload_schema:
        return None
    data_type = getattr(info.payload_schema[field_name], "data_type", None)
    if isinstance(data_type, models.PayloadSchemaType):
        return data_type
    logger.warning("qdrant payload index rejected: field=%s type=missing", field_name)
    raise collection_mismatch()


def _reject_payload_index(info: models.CollectionInfo, field_name: str) -> None:
    data_type = _payload_index_type(info, field_name)
    logger.warning(
        "qdrant payload index rejected: field=%s type=%s",
        field_name,
        data_type,
    )
    raise collection_mismatch()


def _match_field(key: str, value: str) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _match_bool(key: str, value: bool) -> models.FieldCondition:
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
    if not isinstance(point.claim_token, uuid.UUID):
        raise ValueError("claim_token must be a UUID")
    if len(point.vector) != width or not _finite(point.vector):
        raise ValueError("vector length must match the configured dimension")
    chunk_id = evidence_point_id(
        point.source_id,
        digest,
        point.claim_token,
        point.chunk_index,
    )
    payload: dict[str, str | int | bool | None] = {
        "research_id": str(point.research_id),
        "source_id": str(point.source_id),
        "chunk_id": str(chunk_id),
        "chunk_index": point.chunk_index,
        "text": point.text,
        "page_number": point.page_number,
        "url": point.url,
        "title": point.title,
        "content_hash": digest,
        "claim_token": str(point.claim_token),
        "accepted": False,
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
    *,
    accepted_only: bool = False,
) -> EvidenceMatch | None:
    if not isinstance(payload, Mapping) or score is None:
        return None
    if (
        isinstance(score, bool)
        or not isinstance(score, int | float)
        or not math.isfinite(score)
    ):
        return None
    try:
        parsed_research = uuid.UUID(str(payload["research_id"]))
        if parsed_research != research_id:
            return None
        if payload.get("schema_version") != SCHEMA_VERSION:
            return None
        if accepted_only and payload.get("accepted") is not True:
            return None
        text = payload["text"]
        digest = payload["content_hash"]
        claim_token = payload["claim_token"]
        if not isinstance(text, str) or not text.strip():
            return None
        if not isinstance(digest, str) or not isinstance(claim_token, str):
            return None
        uuid.UUID(claim_token)
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
