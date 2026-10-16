"""Retrieval stays inside one research and one accepted digest."""

import uuid
from collections.abc import Sequence

import pytest

from nexus.ai.errors import (
    provider_not_configured,
    provider_timeout,
    provider_unavailable,
)
from nexus.models.source import SourceStatus
from nexus.retrieval.service import RetrievalService
from nexus.retrieval.vector_store import EvidenceMatch
from nexus.retrieval.versions import AcceptedVersion

RESEARCH_ID = uuid.UUID("a8c11b5e-fd31-4c24-9d80-132b317fa7f5")
OTHER_RESEARCH = uuid.UUID("b8c11b5e-fd31-4c24-9d80-132b317fa7f5")
SOURCE_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_SOURCE = uuid.UUID("22222222-2222-4222-8222-222222222222")
OLD_HASH = "ab" * 32
NEW_HASH = "cd" * 32


class Row:
    """The source columns the retrieval service reads."""

    def __init__(
        self,
        *,
        source_id: uuid.UUID,
        research_id: uuid.UUID,
        status: SourceStatus,
        content_hash: str | None,
        indexing_content_hash: str | None = None,
    ) -> None:
        self.id = source_id
        self.research_id = research_id
        self.status = status
        self.content_hash = content_hash
        self.indexing_content_hash = indexing_content_hash


class FakeSession:
    """Return rows whose research id matches the select."""

    def __init__(self, rows: Sequence[Row]) -> None:
        self._rows = list(rows)

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def scalars(self, statement: object) -> _Scalars:
        research_id = statement.whereclause.right.value  # type: ignore[attr-defined]
        matched = [row for row in self._rows if row.research_id == research_id]
        return _Scalars(matched)


class _Scalars:
    def __init__(self, rows: list[Row]) -> None:
        self._rows = rows

    def all(self) -> list[Row]:
        return self._rows


class RecordingEmbedder:
    """Record query embeddings and optionally fail before a vector exists."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.error = error

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [await self.embed_query(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return [0.2, 0.4, 0.6, 0.8]


class RecordingStore:
    """Record the accepted-version filter and return scripted matches."""

    def __init__(
        self,
        matches: Sequence[EvidenceMatch] | None = None,
        *,
        error: Exception | None = None,
        leak: Sequence[EvidenceMatch] | None = None,
    ) -> None:
        self._matches = list(matches or [])
        self._error = error
        self._leak = list(leak) if leak is not None else None
        self.calls: list[tuple[uuid.UUID, int, list[AcceptedVersion]]] = []

    async def search_accepted(
        self,
        *,
        research_id: uuid.UUID,
        vector: Sequence[float],
        limit: int,
        versions: Sequence[AcceptedVersion],
    ) -> list[EvidenceMatch]:
        self.calls.append((research_id, limit, list(versions)))
        if self._error is not None:
            raise self._error
        if self._leak is not None:
            return list(self._leak)[:limit]
        allowed = {(version.source_id, version.content_hash) for version in versions}
        chosen = [
            match
            for match in self._matches
            if match.research_id == research_id
            and (match.source_id, match.content_hash) in allowed
        ]
        chosen.sort(key=lambda match: match.score, reverse=True)
        return chosen[:limit]


def _match(
    *,
    source_id: uuid.UUID = SOURCE_ID,
    research_id: uuid.UUID = RESEARCH_ID,
    score: float = 0.8,
    text: str = "kept passage",
    content_hash: str = NEW_HASH,
    page_number: int | None = 2,
    url: str | None = None,
) -> EvidenceMatch:
    return EvidenceMatch(
        chunk_id=uuid.uuid4(),
        source_id=source_id,
        research_id=research_id,
        score=score,
        text=text,
        page_number=page_number,
        url=url,
        title=None,
        content_hash=content_hash,
        chunk_index=0,
    )


def _service(
    rows: Sequence[Row],
    store: RecordingStore,
    embedder: RecordingEmbedder | None = None,
) -> tuple[RetrievalService, RecordingEmbedder]:
    embedder = embedder or RecordingEmbedder()

    def sessions() -> FakeSession:
        return FakeSession(rows)

    service = RetrievalService(sessions, store, embedder)  # type: ignore[arg-type]
    return service, embedder


async def test_top_k_keeps_the_higher_score() -> None:
    relevant = _match(score=0.91, text="relevant passage")
    other = _match(score=0.2, text="unrelated passage")
    store = RecordingStore([other, relevant])
    service, _embedder = _service(
        [
            Row(
                source_id=SOURCE_ID,
                research_id=RESEARCH_ID,
                status=SourceStatus.INDEXED,
                content_hash=NEW_HASH,
            ),
        ],
        store,
    )

    matches = await service.search(RESEARCH_ID, "relevant passage", 1)

    assert [match.text for match in matches] == ["relevant passage"]
    assert store.calls[0][1] == 1


async def test_unknown_research_does_not_embed() -> None:
    store = RecordingStore([_match()])
    service, embedder = _service(
        [
            Row(
                source_id=SOURCE_ID,
                research_id=RESEARCH_ID,
                status=SourceStatus.INDEXED,
                content_hash=NEW_HASH,
            ),
        ],
        store,
    )

    assert await service.search(OTHER_RESEARCH, "anything", 5) == []
    assert embedder.calls == []
    assert store.calls == []


async def test_another_research_version_is_not_requested() -> None:
    foreign = _match(
        source_id=OTHER_SOURCE,
        research_id=OTHER_RESEARCH,
        text="foreign passage",
        content_hash=OLD_HASH,
    )
    local = _match(text="local passage")
    store = RecordingStore(leak=[foreign, local])
    service, _embedder = _service(
        [
            Row(
                source_id=SOURCE_ID,
                research_id=RESEARCH_ID,
                status=SourceStatus.INDEXED,
                content_hash=NEW_HASH,
            ),
            Row(
                source_id=OTHER_SOURCE,
                research_id=OTHER_RESEARCH,
                status=SourceStatus.INDEXED,
                content_hash=OLD_HASH,
            ),
        ],
        store,
    )

    matches = await service.search(RESEARCH_ID, "local passage", 5)

    versions = store.calls[0][2]
    assert versions == [AcceptedVersion(source_id=SOURCE_ID, content_hash=NEW_HASH)]
    assert [match.text for match in matches] == ["local passage"]


async def test_replaced_digest_is_absent_and_a_failure_keeps_the_last_text() -> None:
    replaced = RecordingStore(
        leak=[
            _match(text="old sentence", content_hash=OLD_HASH, score=0.95),
            _match(text="new sentence", content_hash=NEW_HASH, score=0.4),
        ],
    )
    service, _embedder = _service(
        [
            Row(
                source_id=SOURCE_ID,
                research_id=RESEARCH_ID,
                status=SourceStatus.INDEXED,
                content_hash=NEW_HASH,
            ),
        ],
        replaced,
    )
    current = await service.search(RESEARCH_ID, "new sentence", 5)
    assert [match.text for match in current] == ["new sentence"]

    failed = RecordingStore([_match(text="last good", content_hash=OLD_HASH)])
    service, _embedder = _service(
        [
            Row(
                source_id=SOURCE_ID,
                research_id=RESEARCH_ID,
                status=SourceStatus.FAILED,
                content_hash=OLD_HASH,
                indexing_content_hash=NEW_HASH,
            ),
        ],
        failed,
    )
    kept = await service.search(RESEARCH_ID, "last good", 5)
    assert [match.text for match in kept] == ["last good"]
    assert kept[0].content_hash == OLD_HASH


async def test_missing_provenance_stays_missing() -> None:
    store = RecordingStore([_match(page_number=None, url=None)])
    service, _embedder = _service(
        [
            Row(
                source_id=SOURCE_ID,
                research_id=RESEARCH_ID,
                status=SourceStatus.INDEXED,
                content_hash=NEW_HASH,
            ),
        ],
        store,
    )

    matches = await service.search(RESEARCH_ID, "kept passage", 5)

    assert matches[0].page_number is None
    assert matches[0].url is None


@pytest.mark.parametrize(
    "error",
    [provider_not_configured(), provider_timeout()],
)
async def test_embedding_failure_does_not_search(error: Exception) -> None:
    store = RecordingStore([_match()])
    service, _embedder = _service(
        [
            Row(
                source_id=SOURCE_ID,
                research_id=RESEARCH_ID,
                status=SourceStatus.INDEXED,
                content_hash=NEW_HASH,
            ),
        ],
        store,
        RecordingEmbedder(error=error),
    )

    with pytest.raises(type(error)) as exc_info:
        await service.search(RESEARCH_ID, "kept passage", 5)

    assert exc_info.value is error
    assert store.calls == []


async def test_store_failure_propagates() -> None:
    error = provider_unavailable()
    store = RecordingStore(error=error)
    service, _embedder = _service(
        [
            Row(
                source_id=SOURCE_ID,
                research_id=RESEARCH_ID,
                status=SourceStatus.INDEXED,
                content_hash=NEW_HASH,
            ),
        ],
        store,
    )

    with pytest.raises(type(error)):
        await service.search(RESEARCH_ID, "kept passage", 5)
