"""Qdrant adapter contract against an in-memory client double."""

import uuid
from types import SimpleNamespace

import pytest
from qdrant_client import models
from tests.conftest import make_settings

from nexus.ai.errors import ResearchError
from nexus.retrieval.ids import evidence_point_id
from nexus.retrieval.vector_store import EvidencePoint, EvidenceVectorStore

HASH = "ab" * 32
OTHER = "cd" * 32
WIDTH = 4


class FakeQdrant:
    """Records collection and point calls. It has no delete-collection method."""

    def __init__(self, vectors: object | None = None, *, exists: bool = False) -> None:
        self.vectors = vectors
        self.exists = exists
        self.payload_fields: set[str] = set()
        self.created: list[models.VectorParams] = []
        self.upserts: list[list[models.PointStruct]] = []
        self.deletes: list[models.Filter] = []
        self.searches: list[models.Filter] = []
        self.fail_upsert = False

    async def collection_exists(self, collection_name: str) -> bool:
        return self.exists

    async def get_collection(self, collection_name: str) -> object:
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=self.vectors)),
            payload_schema={name: object() for name in self.payload_fields},
        )

    async def create_collection(
        self,
        collection_name: str,
        vectors_config: models.VectorParams,
    ) -> bool:
        self.created.append(vectors_config)
        self.vectors = vectors_config
        self.exists = True
        return True

    async def create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        *,
        field_schema: models.PayloadSchemaType,
        wait: bool = True,
    ) -> object:
        self.payload_fields.add(field_name)
        return object()

    async def upsert(
        self,
        collection_name: str,
        points: list[models.PointStruct],
        *,
        wait: bool = True,
    ) -> object:
        if self.fail_upsert:
            raise RuntimeError("upsert failed")
        self.upserts.append(list(points))
        return object()

    async def delete(
        self,
        collection_name: str,
        points_selector: models.FilterSelector,
        *,
        wait: bool = True,
    ) -> object:
        self.deletes.append(points_selector.filter)
        return object()

    async def query_points(
        self,
        collection_name: str,
        query: list[float],
        *,
        query_filter: models.Filter,
        limit: int,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> object:
        self.searches.append(query_filter)
        return SimpleNamespace(points=[])

    async def close(self) -> None:
        return None


def _store(client: FakeQdrant, *, size: int = WIDTH) -> EvidenceVectorStore:
    settings = make_settings(rag_collection="nexus_evidence_it", rag_vector_size=size)
    return EvidenceVectorStore(settings, client)  # type: ignore[arg-type]


def _point(
    research_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    content_hash: str = HASH,
    chunk_index: int = 0,
) -> EvidencePoint:
    return EvidencePoint(
        research_id=research_id,
        source_id=source_id,
        chunk_index=chunk_index,
        text="passage",
        vector=[0.1, 0.2, 0.3, 0.4],
        content_hash=content_hash,
    )


def _values(condition_filter: models.Filter) -> dict[str, str]:
    found: dict[str, str] = {}
    if condition_filter.must is None:
        return found
    must = condition_filter.must
    conditions = must if isinstance(must, list) else []
    for condition in conditions:
        if isinstance(condition, models.FieldCondition) and isinstance(
            condition.match,
            models.MatchValue,
        ):
            found[condition.key] = str(condition.match.value)
    return found


def test_point_ids_follow_the_source_hash_and_chunk() -> None:
    source_id = uuid.uuid4()
    first = evidence_point_id(source_id, HASH, 0)
    assert first == evidence_point_id(source_id, HASH, 0)
    assert first != evidence_point_id(source_id, HASH, 1)
    assert first != evidence_point_id(source_id, OTHER, 0)
    with pytest.raises(ValueError, match="chunk_index"):
        evidence_point_id(source_id, HASH, -1)


async def test_missing_collection_is_created_with_cosine_and_payload_indexes() -> None:
    client = FakeQdrant()
    await _store(client).ensure_collection()

    assert len(client.created) == 1
    assert client.created[0].size == WIDTH
    assert client.created[0].distance is models.Distance.COSINE
    assert client.payload_fields == {"research_id", "source_id"}


async def test_mismatched_collection_is_left_in_place() -> None:
    client = FakeQdrant(
        models.VectorParams(size=3, distance=models.Distance.EUCLID),
        exists=True,
    )
    with pytest.raises(ResearchError) as exc_info:
        await _store(client).ensure_collection()

    assert exc_info.value.code == "collection_mismatch"
    assert client.created == []
    assert client.exists is True
    assert not hasattr(client, "delete_collection")


async def test_named_vectors_are_rejected() -> None:
    client = FakeQdrant(
        {"text": models.VectorParams(size=WIDTH, distance=models.Distance.COSINE)},
        exists=True,
    )
    with pytest.raises(ResearchError) as exc_info:
        await _store(client).ensure_collection()
    assert exc_info.value.code == "collection_mismatch"


async def test_upsert_uses_stable_ids_and_rejects_another_research() -> None:
    client = FakeQdrant()
    research_id = uuid.uuid4()
    source_id = uuid.uuid4()
    store = _store(client)
    await store.upsert(research_id=research_id, points=[_point(research_id, source_id)])

    point = client.upserts[0][0]
    expected = evidence_point_id(source_id, HASH, 0)
    assert point.id == str(expected)
    assert point.payload is not None
    assert point.payload["chunk_id"] == str(expected)
    assert point.payload["schema_version"] == 1
    assert point.payload["research_id"] == str(research_id)

    with pytest.raises(ValueError, match="research_id"):
        await store.upsert(
            research_id=research_id,
            points=[_point(uuid.uuid4(), source_id)],
        )
    assert len(client.upserts) == 1


async def test_search_filter_always_includes_research_id() -> None:
    client = FakeQdrant(
        models.VectorParams(size=WIDTH, distance=models.Distance.COSINE),
        exists=True,
    )
    client.payload_fields.update({"research_id", "source_id"})
    research_id = uuid.uuid4()
    await _store(client).search(
        research_id=research_id,
        vector=[0.1, 0.2, 0.3, 0.4],
        limit=5,
        content_hash=HASH,
    )

    assert _values(client.searches[0]) == {
        "research_id": str(research_id),
        "content_hash": HASH,
    }


async def test_delete_filters_include_research_and_source() -> None:
    client = FakeQdrant(
        models.VectorParams(size=WIDTH, distance=models.Distance.COSINE),
        exists=True,
    )
    client.payload_fields.update({"research_id", "source_id"})
    research_id = uuid.uuid4()
    source_id = uuid.uuid4()
    store = _store(client)
    await store.delete_except_hash(
        research_id=research_id,
        source_id=source_id,
        content_hash=HASH,
    )

    deleted = client.deletes[0]
    assert _values(deleted) == {
        "research_id": str(research_id),
        "source_id": str(source_id),
    }
    assert deleted.must_not is not None
    excluded = deleted.must_not[0]
    assert isinstance(excluded, models.FieldCondition)
    assert excluded.key == "content_hash"


async def test_failed_upsert_does_not_hide_the_provider_error() -> None:
    client = FakeQdrant()
    client.fail_upsert = True
    research_id = uuid.uuid4()
    with pytest.raises(ResearchError) as exc_info:
        await _store(client).upsert(
            research_id=research_id,
            points=[_point(research_id, uuid.uuid4())],
        )
    assert exc_info.value.code == "provider_unavailable"


async def test_malformed_payloads_are_dropped() -> None:
    research_id = uuid.uuid4()
    source_id = uuid.uuid4()
    chunk_id = evidence_point_id(source_id, HASH, 0)
    good = {
        "research_id": str(research_id),
        "source_id": str(source_id),
        "chunk_id": str(chunk_id),
        "chunk_index": 0,
        "text": "kept",
        "page_number": 2,
        "url": None,
        "title": "Title",
        "content_hash": HASH,
        "schema_version": 1,
    }
    client = FakeQdrant(
        models.VectorParams(size=WIDTH, distance=models.Distance.COSINE),
        exists=True,
    )
    client.payload_fields.update({"research_id", "source_id"})

    async def query_points(
        collection_name: str,
        query: list[float],
        **kwargs: object,
    ) -> object:
        client.searches.append(kwargs["query_filter"])  # type: ignore[arg-type]
        return SimpleNamespace(
            points=[
                SimpleNamespace(score=0.4, payload={"text": "missing"}),
                SimpleNamespace(score=0.8, payload=good),
                SimpleNamespace(
                    score=0.1,
                    payload={**good, "research_id": str(uuid.uuid4())},
                ),
            ],
        )

    client.query_points = query_points  # type: ignore[method-assign]
    matches = await _store(client).search(
        research_id=research_id,
        vector=[0.1, 0.2, 0.3, 0.4],
        limit=5,
    )

    assert len(matches) == 1
    assert matches[0].text == "kept"
    assert matches[0].page_number == 2
    assert matches[0].chunk_id == chunk_id
    assert matches[0].research_id == research_id
