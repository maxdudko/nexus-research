"""Research-scoped Qdrant writes using deterministic vectors and PostgreSQL claims."""

import hashlib
import uuid

import pytest
from qdrant_client import models
from tests.conftest import make_settings
from tests.integration.conftest import require_local_qdrant, run_alembic

from nexus.ai.errors import ResearchError
from nexus.core.config import get_settings
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.models.source import Source, SourceStatus, SourceType
from nexus.retrieval.ids import evidence_point_id
from nexus.retrieval.vector_store import (
    EvidencePoint,
    EvidenceVectorStore,
    create_qdrant_client,
)
from nexus.sources.lifecycle import (
    ClaimAction,
    StaleClaim,
    claim_source,
    finish_failed,
    finish_indexed,
)

pytestmark = pytest.mark.integration

WIDTH = 8
COLLECTION_PREFIX = "nexus_evidence_it_"
APPLICATION_COLLECTION = "nexus_evidence_v1"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    values = [byte / 255 for byte in digest]
    copies = (WIDTH // len(values)) + 1
    return (values * copies)[:WIDTH]


def _collection_name() -> str:
    name = f"{COLLECTION_PREFIX}{uuid.uuid4().hex}"
    if name == APPLICATION_COLLECTION or not name.startswith(COLLECTION_PREFIX):
        raise RuntimeError("refusing to use the application collection")
    return name


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


@pytest.fixture
async def evidence_store():
    configured = str(get_settings().qdrant_url)
    require_local_qdrant(configured)
    name = _collection_name()
    settings = make_settings(
        qdrant_url=configured,
        rag_collection=name,
        rag_vector_size=WIDTH,
    )
    client = create_qdrant_client(settings)
    store = EvidenceVectorStore(settings, client)
    try:
        yield store, client, name
    finally:
        if name.startswith(COLLECTION_PREFIX) and name != APPLICATION_COLLECTION:
            if await client.collection_exists(name):
                await client.delete_collection(name)
        await client.close()


def _passage(
    research_id: uuid.UUID,
    source_id: uuid.UUID,
    text: str,
    content_hash: str,
    *,
    chunk_index: int = 0,
) -> EvidencePoint:
    return EvidencePoint(
        research_id=research_id,
        source_id=source_id,
        chunk_index=chunk_index,
        text=text,
        vector=_vector(text),
        content_hash=content_hash,
        page_number=2,
        url="https://example.org/article",
        title="Article",
    )


async def test_research_filter_replacement_and_idempotent_upsert(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, object, str],
) -> None:
    store, client, name = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    other_research = uuid.uuid4()
    first_text = "alpha evidence"
    second_text = "beta evidence"
    first_hash = _hash(first_text)
    second_hash = _hash(second_text)
    try:
        async with factory() as session:
            async with session.begin():
                claim = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/article",
                    content_hash=first_hash,
                )
            assert claim.token is not None
            source_id = claim.source_id
            token = claim.token

        await store.upsert(
            research_id=research_id,
            points=[_passage(research_id, source_id, first_text, first_hash)],
        )
        await store.upsert(
            research_id=research_id,
            points=[_passage(research_id, source_id, first_text, first_hash)],
        )
        counted = await client.count(name, exact=True)
        assert counted.count == 1

        async with factory() as session:
            async with session.begin():
                await finish_indexed(
                    session,
                    source_id=source_id,
                    token=token,
                    content_hash=first_hash,
                    chunk_count=1,
                )

        own = await store.search(
            research_id=research_id,
            vector=_vector(first_text),
            limit=5,
        )
        foreign = await store.search(
            research_id=other_research,
            vector=_vector(first_text),
            limit=5,
        )
        assert [match.text for match in own] == [first_text]
        assert own[0].page_number == 2
        assert own[0].url == "https://example.org/article"
        assert own[0].chunk_id == evidence_point_id(source_id, first_hash, 0)
        assert foreign == []

        async with factory() as session:
            async with session.begin():
                replacement = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/article",
                    content_hash=second_hash,
                )
            assert replacement.token is not None
            assert replacement.source_id == source_id

        await store.upsert(
            research_id=research_id,
            points=[_passage(research_id, source_id, second_text, second_hash)],
        )
        both = await store.search(
            research_id=research_id,
            vector=_vector(first_text),
            limit=5,
        )
        assert {match.content_hash for match in both} == {first_hash, second_hash}

        await store.delete_except_hash(
            research_id=research_id,
            source_id=source_id,
            content_hash=second_hash,
        )
        async with factory() as session:
            async with session.begin():
                await finish_indexed(
                    session,
                    source_id=source_id,
                    token=replacement.token,
                    content_hash=second_hash,
                    chunk_count=1,
                )
            source = await session.get(Source, source_id)

        stale = await store.search(
            research_id=research_id,
            vector=_vector(first_text),
            limit=5,
            content_hash=first_hash,
        )
        current = await store.search(
            research_id=research_id,
            vector=_vector(second_text),
            limit=5,
            content_hash=second_hash,
        )
        assert stale == []
        assert [match.text for match in current] == [second_text]
        assert source is not None
        assert source.status is SourceStatus.INDEXED
        assert source.content_hash == second_hash
        info = await client.get_collection(name)
        assert set(info.payload_schema) >= {"research_id", "source_id"}
    finally:
        await engine.dispose()


async def test_upsert_failure_does_not_mark_the_source_indexed(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, object, str],
) -> None:
    store, client, _name = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    content_hash = _hash("failed attempt")

    async def fail_upsert(*args: object, **kwargs: object) -> None:
        raise RuntimeError("upsert failed")

    client.upsert = fail_upsert  # type: ignore[method-assign]
    try:
        async with factory() as session:
            async with session.begin():
                claim = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/fail",
                    content_hash=content_hash,
                )
            assert claim.token is not None
            with pytest.raises(ResearchError) as exc_info:
                await store.upsert(
                    research_id=research_id,
                    points=[
                        _passage(
                            research_id,
                            claim.source_id,
                            "failed attempt",
                            content_hash,
                        )
                    ],
                )
            assert exc_info.value.code == "provider_unavailable"
            async with session.begin():
                await finish_failed(
                    session,
                    source_id=claim.source_id,
                    token=claim.token,
                    error_code="vector_upsert_failed",
                )
            source = await session.get(Source, claim.source_id)
        assert source is not None
        assert source.status is SourceStatus.FAILED
        assert source.content_hash is None
    finally:
        await engine.dispose()


async def test_losing_claim_deletes_only_its_own_hash(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, object, str],
) -> None:
    store, _client, _name = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    first_text = "first attempt"
    second_text = "second attempt"
    first_hash = _hash(first_text)
    second_hash = _hash(second_text)
    try:
        async with factory() as session:
            async with session.begin():
                first = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/race",
                    content_hash=first_hash,
                )
            assert first.token is not None
            await store.upsert(
                research_id=research_id,
                points=[_passage(research_id, first.source_id, first_text, first_hash)],
            )
            async with session.begin():
                second = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/race",
                    content_hash=second_hash,
                )
            assert second.action is ClaimAction.CLAIMED
            assert second.token is not None
            assert second.token != first.token
            await store.upsert(
                research_id=research_id,
                points=[
                    _passage(research_id, first.source_id, second_text, second_hash),
                ],
            )
            await store.delete_hash(
                research_id=research_id,
                source_id=first.source_id,
                content_hash=first_hash,
            )
            with pytest.raises(StaleClaim):
                async with session.begin():
                    await finish_indexed(
                        session,
                        source_id=first.source_id,
                        token=first.token,
                        content_hash=first_hash,
                        chunk_count=1,
                    )
            await store.delete_except_hash(
                research_id=research_id,
                source_id=first.source_id,
                content_hash=second_hash,
            )
            async with session.begin():
                await finish_indexed(
                    session,
                    source_id=first.source_id,
                    token=second.token,
                    content_hash=second_hash,
                    chunk_count=1,
                )

        removed = await store.search(
            research_id=research_id,
            vector=_vector(first_text),
            limit=5,
            content_hash=first_hash,
        )
        kept = await store.search(
            research_id=research_id,
            vector=_vector(second_text),
            limit=5,
            content_hash=second_hash,
        )
        assert removed == []
        assert [match.text for match in kept] == [second_text]
    finally:
        await engine.dispose()


async def test_mismatched_collection_is_not_recreated(
    evidence_store: tuple[EvidenceVectorStore, object, str],
) -> None:
    _store, client, name = evidence_store
    await client.create_collection(
        collection_name=name,
        vectors_config=models.VectorParams(size=4, distance=models.Distance.EUCLID),
    )
    settings = make_settings(
        qdrant_url=str(get_settings().qdrant_url),
        rag_collection=name,
        rag_vector_size=WIDTH,
    )
    store = EvidenceVectorStore(settings, client)
    with pytest.raises(ResearchError) as exc_info:
        await store.ensure_collection()
    assert exc_info.value.code == "collection_mismatch"
    info = await client.get_collection(name)
    vectors = info.config.params.vectors
    assert isinstance(vectors, models.VectorParams)
    assert vectors.size == 4
    assert vectors.distance is models.Distance.EUCLID
