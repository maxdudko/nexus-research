"""Qdrant adapter contract against an in-memory client double."""

import uuid
from types import SimpleNamespace

import pytest
from qdrant_client import models
from tests.conftest import make_settings

from nexus.ai.errors import ResearchError
from nexus.retrieval.ids import evidence_point_id
from nexus.retrieval.vector_store import EvidencePoint, EvidenceVectorStore
from nexus.retrieval.versions import AcceptedVersion

HASH = "ab" * 32
OTHER = "cd" * 32
WIDTH = 4
TOKEN = uuid.UUID("11111111-1111-4111-8111-111111111111")
KEYWORD_INDEXES = {
    "research_id": models.PayloadSchemaType.KEYWORD,
    "source_id": models.PayloadSchemaType.KEYWORD,
}


class FakeQdrant:
    """Records collection and point calls. It has no delete-collection method."""

    def __init__(
        self,
        vectors: object | None = None,
        *,
        exists: bool = False,
        payload_types: dict[str, models.PayloadSchemaType] | None = None,
    ) -> None:
        self.vectors = vectors
        self.exists = exists
        self.payload_types = dict(payload_types or {})
        self.created: list[models.VectorParams] = []
        self.index_calls: list[str] = []
        self.upserts: list[list[models.PointStruct]] = []
        self.deletes: list[models.Filter] = []
        self.searches: list[models.Filter] = []
        self.fail_upsert = False

    async def collection_exists(self, collection_name: str) -> bool:
        return self.exists

    async def get_collection(self, collection_name: str) -> object:
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=self.vectors)),
            payload_schema={
                name: models.PayloadIndexInfo(data_type=data_type, points=0)
                for name, data_type in self.payload_types.items()
            },
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
        self.index_calls.append(field_name)
        self.payload_types[field_name] = field_schema
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
    claim_token: uuid.UUID = TOKEN,
) -> EvidencePoint:
    return EvidencePoint(
        research_id=research_id,
        source_id=source_id,
        chunk_index=chunk_index,
        text="passage",
        vector=[0.1, 0.2, 0.3, 0.4],
        content_hash=content_hash,
        claim_token=claim_token,
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


def test_point_ids_follow_the_source_hash_token_and_chunk() -> None:
    source_id = uuid.uuid4()
    other_token = uuid.uuid4()
    first = evidence_point_id(source_id, HASH, TOKEN, 0)
    assert first == evidence_point_id(source_id, HASH, TOKEN, 0)
    assert first != evidence_point_id(source_id, HASH, TOKEN, 1)
    assert first != evidence_point_id(source_id, OTHER, TOKEN, 0)
    assert first != evidence_point_id(source_id, HASH, other_token, 0)
    with pytest.raises(ValueError, match="chunk_index"):
        evidence_point_id(source_id, HASH, TOKEN, -1)


async def test_missing_collection_is_created_with_cosine_and_payload_indexes() -> None:
    client = FakeQdrant()
    await _store(client).ensure_collection()

    assert len(client.created) == 1
    assert client.created[0].size == WIDTH
    assert client.created[0].distance is models.Distance.COSINE
    assert set(client.payload_types) == {"research_id", "source_id"}
    assert set(client.payload_types.values()) == {models.PayloadSchemaType.KEYWORD}


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
    expected = evidence_point_id(source_id, HASH, TOKEN, 0)
    assert point.id == str(expected)
    assert point.payload is not None
    assert point.payload["chunk_id"] == str(expected)
    assert point.payload["schema_version"] == 1
    assert point.payload["research_id"] == str(research_id)
    assert point.payload["claim_token"] == str(TOKEN)
    assert point.payload["accepted"] is False

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
        payload_types=dict(KEYWORD_INDEXES),
    )
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


async def test_claim_deletes_match_the_token() -> None:
    client = FakeQdrant(
        models.VectorParams(size=WIDTH, distance=models.Distance.COSINE),
        exists=True,
        payload_types=dict(KEYWORD_INDEXES),
    )
    research_id = uuid.uuid4()
    source_id = uuid.uuid4()
    store = _store(client)
    await store.delete_claim(
        research_id=research_id,
        source_id=source_id,
        claim_token=TOKEN,
    )
    await store.delete_other_claims(
        research_id=research_id,
        source_id=source_id,
        claim_token=TOKEN,
    )

    own = client.deletes[0]
    assert _values(own) == {
        "research_id": str(research_id),
        "source_id": str(source_id),
        "claim_token": str(TOKEN),
    }
    assert own.must_not is None
    others = client.deletes[1]
    assert _values(others) == {
        "research_id": str(research_id),
        "source_id": str(source_id),
    }
    assert others.must_not is not None
    excluded = others.must_not[0]
    assert isinstance(excluded, models.FieldCondition)
    assert excluded.key == "claim_token"
    assert isinstance(excluded.match, models.MatchValue)
    assert excluded.match.value == str(TOKEN)


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
    chunk_id = evidence_point_id(source_id, HASH, TOKEN, 0)
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
        "claim_token": str(TOKEN),
        "schema_version": 1,
    }
    client = FakeQdrant(
        models.VectorParams(size=WIDTH, distance=models.Distance.COSINE),
        exists=True,
    )
    client.payload_types.update(KEYWORD_INDEXES)

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
                SimpleNamespace(
                    score=0.2,
                    payload={
                        key: value
                        for key, value in good.items()
                        if key != "claim_token"
                    },
                ),
                SimpleNamespace(score=float("nan"), payload=good),
                SimpleNamespace(score=float("inf"), payload=good),
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


def _ready_client(
    payload_types: dict[str, models.PayloadSchemaType] | None = None,
) -> FakeQdrant:
    return FakeQdrant(
        models.VectorParams(size=WIDTH, distance=models.Distance.COSINE),
        exists=True,
        payload_types=dict(KEYWORD_INDEXES if payload_types is None else payload_types),
    )


async def test_keyword_payload_indexes_are_reused() -> None:
    client = _ready_client()
    await _store(client).ensure_collection()

    assert client.index_calls == []
    assert client.created == []


async def test_non_keyword_payload_index_is_left_in_place() -> None:
    client = _ready_client(
        {
            "research_id": models.PayloadSchemaType.INTEGER,
            "source_id": models.PayloadSchemaType.KEYWORD,
        },
    )
    with pytest.raises(ResearchError) as exc_info:
        await _store(client).ensure_collection()

    assert exc_info.value.code == "collection_mismatch"
    assert client.created == []
    assert client.index_calls == []
    assert client.payload_types["research_id"] is models.PayloadSchemaType.INTEGER
    assert not hasattr(client, "delete_collection")


async def test_empty_accepted_versions_do_not_query() -> None:
    client = _ready_client()
    matches = await _store(client).search_accepted(
        research_id=uuid.uuid4(),
        vector=[0.1, 0.2, 0.3, 0.4],
        limit=5,
        versions=[],
    )

    assert matches == []
    assert client.searches == []


async def test_search_accepted_requires_each_version() -> None:
    client = _ready_client()
    research_id = uuid.uuid4()
    first_source = uuid.uuid4()
    second_source = uuid.uuid4()
    await _store(client).search_accepted(
        research_id=research_id,
        vector=[0.1, 0.2, 0.3, 0.4],
        limit=5,
        versions=[
            AcceptedVersion(source_id=first_source, content_hash=HASH),
            AcceptedVersion(source_id=second_source, content_hash=OTHER),
        ],
    )

    query = client.searches[0]
    assert _values(query) == {
        "research_id": str(research_id),
        "accepted": "True",
    }
    assert query.must is not None
    accepted = [
        condition
        for condition in query.must
        if isinstance(condition, models.FieldCondition) and condition.key == "accepted"
    ]
    assert len(accepted) == 1
    assert isinstance(accepted[0].match, models.MatchValue)
    assert accepted[0].match.value is True
    assert query.min_should is not None
    assert query.min_should.min_count == 1
    assert query.should is not None
    should = query.should if isinstance(query.should, list) else []
    pairs = [_values(item) for item in should if isinstance(item, models.Filter)]
    assert pairs == [
        {"source_id": str(first_source), "content_hash": HASH},
        {"source_id": str(second_source), "content_hash": OTHER},
    ]
