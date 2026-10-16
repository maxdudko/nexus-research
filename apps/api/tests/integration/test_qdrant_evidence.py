"""Research-scoped Qdrant writes using deterministic vectors and PostgreSQL claims."""

import asyncio
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
from nexus.retrieval.versions import AcceptedVersion
from nexus.sources.lifecycle import (
    ClaimAction,
    StaleClaim,
    abandon_claim,
    claim_source,
    finish_failed,
    finish_indexed,
    retire_other_claims,
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
    claim_token: uuid.UUID,
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
        claim_token=claim_token,
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
            points=[_passage(research_id, source_id, first_text, first_hash, token)],
        )
        await store.upsert(
            research_id=research_id,
            points=[_passage(research_id, source_id, first_text, first_hash, token)],
        )
        counted = await client.count(name, exact=True)
        assert counted.count == 1

        async with factory() as session:
            async with session.begin():
                await store.accept_claim(
                    research_id=research_id,
                    source_id=source_id,
                    claim_token=token,
                )
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
        assert own[0].chunk_id == evidence_point_id(source_id, first_hash, token, 0)
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
            points=[
                _passage(
                    research_id,
                    source_id,
                    second_text,
                    second_hash,
                    replacement.token,
                ),
            ],
        )
        both = await store.search(
            research_id=research_id,
            vector=_vector(first_text),
            limit=5,
        )
        accepted = await store.search_accepted(
            research_id=research_id,
            vector=_vector(second_text),
            limit=5,
            versions=[AcceptedVersion(source_id=source_id, content_hash=second_hash)],
        )
        assert {match.content_hash for match in both} == {first_hash, second_hash}
        assert accepted == []

        async with factory() as session:
            async with session.begin():
                await retire_other_claims(
                    session,
                    store,
                    source_id=source_id,
                    token=replacement.token,
                )
                await finish_indexed(
                    session,
                    source_id=source_id,
                    token=replacement.token,
                    content_hash=second_hash,
                    chunk_count=1,
                )
            source = await session.get(Source, source_id)

        preserved = await store.search(
            research_id=research_id,
            vector=_vector(first_text),
            limit=5,
            content_hash=first_hash,
        )
        assert [match.text for match in preserved] == [first_text]
        await store.delete_other_claims(
            research_id=research_id,
            source_id=source_id,
            claim_token=replacement.token,
        )
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
        visible = await store.search_accepted(
            research_id=research_id,
            vector=_vector(second_text),
            limit=5,
            versions=[AcceptedVersion(source_id=source_id, content_hash=second_hash)],
        )
        assert [match.text for match in visible] == [second_text]
        assert visible[0].content_hash == second_hash
        assert source is not None
        assert source.status is SourceStatus.INDEXED
        assert source.content_hash == second_hash
        info = await client.get_collection(name)
        for field_name in ("research_id", "source_id"):
            assert (
                info.payload_schema[field_name].data_type
                is models.PayloadSchemaType.KEYWORD
            )
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
                            claim.token,
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


async def test_stale_claim_cannot_delete_newer_points(
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
                points=[
                    _passage(
                        research_id,
                        first.source_id,
                        first_text,
                        first_hash,
                        first.token,
                    ),
                ],
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
                    _passage(
                        research_id,
                        first.source_id,
                        second_text,
                        second_hash,
                        second.token,
                    ),
                ],
            )
            accepted = await store.search_accepted(
                research_id=research_id,
                vector=_vector(second_text),
                limit=5,
                versions=[
                    AcceptedVersion(
                        source_id=first.source_id,
                        content_hash=second_hash,
                    ),
                ],
            )
            stored = await store.search(
                research_id=research_id,
                vector=_vector(first_text),
                limit=5,
            )
            assert accepted == []
            assert {match.content_hash for match in stored} == {first_hash, second_hash}
            with pytest.raises(StaleClaim):
                async with session.begin():
                    await retire_other_claims(
                        session,
                        store,
                        source_id=first.source_id,
                        token=first.token,
                    )
            after_reject = await store.search(
                research_id=research_id,
                vector=_vector(first_text),
                limit=5,
            )
            assert {match.content_hash for match in after_reject} == {
                first_hash,
                second_hash,
            }
            await abandon_claim(
                store,
                research_id=research_id,
                source_id=first.source_id,
                token=first.token,
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


async def test_replaced_token_cannot_delete_a_reused_digest(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, object, str],
) -> None:
    store, client, name = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    text = "same evidence"
    digest = _hash(text)
    try:
        async with factory() as session:
            async with session.begin():
                first = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/same",
                    content_hash=digest,
                )
            assert first.token is not None
            await store.upsert(
                research_id=research_id,
                points=[
                    _passage(research_id, first.source_id, text, digest, first.token),
                ],
            )
            async with session.begin():
                second = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/same",
                    content_hash=digest,
                )
            assert second.token is not None
            assert second.token != first.token
            await store.upsert(
                research_id=research_id,
                points=[
                    _passage(research_id, first.source_id, text, digest, second.token),
                ],
            )
        await abandon_claim(
            store,
            research_id=research_id,
            source_id=first.source_id,
            token=first.token,
        )
        point_id = str(evidence_point_id(first.source_id, digest, second.token, 0))
        records = await client.retrieve(collection_name=name, ids=[point_id])
        kept = await store.search(
            research_id=research_id,
            vector=_vector(text),
            limit=5,
            content_hash=digest,
        )
        assert len(records) == 1
        assert records[0].payload is not None
        assert records[0].payload["claim_token"] == str(second.token)
        assert [match.text for match in kept] == [text]
    finally:
        await engine.dispose()


async def test_concurrent_retire_keeps_the_winning_token(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, object, str],
) -> None:
    store, _client, _name = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    first_text = "older evidence"
    second_text = "newer evidence"
    first_hash = _hash(first_text)
    second_hash = _hash(second_text)
    try:
        async with factory() as session:
            async with session.begin():
                first = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/concurrent",
                    content_hash=first_hash,
                )
            assert first.token is not None
            await store.upsert(
                research_id=research_id,
                points=[
                    _passage(
                        research_id,
                        first.source_id,
                        first_text,
                        first_hash,
                        first.token,
                    ),
                ],
            )
            async with session.begin():
                second = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/concurrent",
                    content_hash=second_hash,
                )
            assert second.token is not None
            await store.upsert(
                research_id=research_id,
                points=[
                    _passage(
                        research_id,
                        first.source_id,
                        second_text,
                        second_hash,
                        second.token,
                    ),
                ],
            )

        async def retire(token: uuid.UUID) -> None:
            async with factory() as session:
                async with session.begin():
                    await retire_other_claims(
                        session,
                        store,
                        source_id=first.source_id,
                        token=token,
                    )

        outcomes = await asyncio.gather(
            retire(first.token),
            retire(second.token),
            return_exceptions=True,
        )
        assert sum(isinstance(item, StaleClaim) for item in outcomes) == 1
        assert sum(item is None for item in outcomes) == 1
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
        assert [match.text for match in removed] == [first_text]
        assert [match.text for match in kept] == [second_text]
        await store.delete_other_claims(
            research_id=research_id,
            source_id=first.source_id,
            claim_token=second.token,
        )
        still_unaccepted = await store.search(
            research_id=research_id,
            vector=_vector(first_text),
            limit=5,
            content_hash=first_hash,
        )
        assert [match.text for match in still_unaccepted] == [first_text]
        await abandon_claim(
            store,
            research_id=research_id,
            source_id=first.source_id,
            token=first.token,
        )
        after_abandon = await store.search(
            research_id=research_id,
            vector=_vector(first_text),
            limit=5,
            content_hash=first_hash,
        )
        winner = await store.search(
            research_id=research_id,
            vector=_vector(second_text),
            limit=5,
            content_hash=second_hash,
        )
        assert after_abandon == []
        assert [match.text for match in winner] == [second_text]
    finally:
        await engine.dispose()


async def test_late_upsert_does_not_replace_accepted_points(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, object, str],
) -> None:
    store, client, name = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    text = "accepted evidence"
    digest = _hash(text)
    try:
        async with factory() as session:
            async with session.begin():
                first = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/late",
                    content_hash=digest,
                )
            assert first.token is not None
            await store.upsert(
                research_id=research_id,
                points=[
                    _passage(research_id, first.source_id, text, digest, first.token),
                ],
            )
            async with session.begin():
                second = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri="https://example.org/late",
                    content_hash=digest,
                )
            assert second.token is not None
            await store.upsert(
                research_id=research_id,
                points=[
                    _passage(research_id, first.source_id, text, digest, second.token),
                ],
            )
            async with session.begin():
                await retire_other_claims(
                    session,
                    store,
                    source_id=first.source_id,
                    token=second.token,
                )
                await finish_indexed(
                    session,
                    source_id=first.source_id,
                    token=second.token,
                    content_hash=digest,
                    chunk_count=1,
                )
        winner_id = str(evidence_point_id(first.source_id, digest, second.token, 0))
        before = await client.retrieve(
            collection_name=name,
            ids=[winner_id],
            with_payload=True,
            with_vectors=True,
        )
        await store.upsert(
            research_id=research_id,
            points=[
                _passage(
                    research_id,
                    first.source_id,
                    "stale replacement",
                    digest,
                    first.token,
                ),
            ],
        )
        after = await client.retrieve(
            collection_name=name,
            ids=[winner_id],
            with_payload=True,
            with_vectors=True,
        )
        assert len(before) == 1
        assert len(after) == 1
        assert after[0].payload is not None
        assert after[0].payload["text"] == text
        assert after[0].payload["claim_token"] == str(second.token)
        assert after[0].payload["accepted"] is True
        assert after[0].vector == before[0].vector
        with pytest.raises(StaleClaim):
            async with factory() as session:
                async with session.begin():
                    await retire_other_claims(
                        session,
                        store,
                        source_id=first.source_id,
                        token=first.token,
                    )
        await abandon_claim(
            store,
            research_id=research_id,
            source_id=first.source_id,
            token=first.token,
        )
        loser_id = str(evidence_point_id(first.source_id, digest, first.token, 0))
        loser = await client.retrieve(collection_name=name, ids=[loser_id])
        winner = await client.retrieve(collection_name=name, ids=[winner_id])
        assert loser == []
        assert len(winner) == 1
        assert winner[0].payload is not None
        assert winner[0].payload["accepted"] is True
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
