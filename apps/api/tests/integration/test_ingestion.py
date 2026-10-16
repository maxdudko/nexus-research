"""PDF and web ingestion against local PostgreSQL and Qdrant."""

import hashlib
import uuid
from collections.abc import Sequence

import pymupdf
import pytest
from sqlalchemy import select
from tests.conftest import make_settings
from tests.integration.conftest import require_local_qdrant, run_alembic

from nexus.ai.errors import ResearchError, provider_unavailable
from nexus.core.config import Settings, get_settings
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.ingestion.service import IngestionService
from nexus.ingestion.transport import FetchedPage
from nexus.models.source import Source, SourceStatus
from nexus.retrieval.vector_store import (
    EvidenceVectorStore,
    create_qdrant_client,
)
from nexus.retrieval.versions import AcceptedVersion

pytestmark = pytest.mark.integration

WIDTH = 8
COLLECTION_PREFIX = "nexus_evidence_it_"
APPLICATION_COLLECTION = "nexus_evidence_v1"
ARTICLE = """<!DOCTYPE html><html><head><title>{title}</title></head>
<body><article><h1>{title}</h1><p>{body}</p></article></body></html>"""


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    values = [byte / 255 for byte in digest]
    copies = (WIDTH // len(values)) + 1
    return (values * copies)[:WIDTH]


class DeterministicEmbedder:
    """Map text to a stable vector without calling OpenAI."""

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return _vector(text)


def _pdf(pages: list[str]) -> bytes:
    document = pymupdf.open()
    try:
        for text in pages:
            document.new_page().insert_text((72, 72), text)
        return document.tobytes()
    finally:
        document.close()


@pytest.fixture
async def evidence_store():
    configured = str(get_settings().qdrant_url)
    require_local_qdrant(configured)
    name = f"{COLLECTION_PREFIX}{uuid.uuid4().hex}"
    settings = make_settings(
        qdrant_url=configured,
        rag_collection=name,
        rag_vector_size=WIDTH,
    )
    client = create_qdrant_client(settings)
    store = EvidenceVectorStore(settings, client)
    try:
        yield store, settings
    finally:
        if name.startswith(COLLECTION_PREFIX) and name != APPLICATION_COLLECTION:
            if await client.collection_exists(name):
                await client.delete_collection(name)
        await client.close()


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


async def test_pdf_ingestion_is_idempotent_and_keeps_page_numbers(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, Settings],
) -> None:
    store, settings = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    service = IngestionService(factory, store, DeterministicEmbedder(), settings)
    research_id = uuid.uuid4()
    data = _pdf(
        [
            "First page explains the indexing token.",
            "Second page explains the accepted digest.",
        ],
    )
    try:
        first = await service.ingest_pdf(research_id, data, "notes.pdf")
        second = await service.ingest_pdf(research_id, data, "notes.pdf")
        async with factory() as session:
            source = await session.get(Source, first.source_id)
        assert source is not None
        assert source.content_hash is not None
        visible = await store.search_accepted(
            research_id=research_id,
            vector=_vector("Second page explains the accepted digest."),
            limit=5,
            versions=[
                AcceptedVersion(
                    source_id=first.source_id,
                    content_hash=source.content_hash,
                ),
            ],
        )
        stored = await store.search(
            research_id=research_id,
            vector=_vector("First page explains the indexing token."),
            limit=10,
        )
        assert first.deduplicated is False
        assert first.chunk_count == 2
        assert second.deduplicated is True
        assert second.source_id == first.source_id
        assert second.chunk_count == 2
        pages = {match.page_number: match.text for match in visible}
        assert pages[1].startswith("First page")
        assert "Second page" in pages[2]
        assert all(match.url is None for match in visible)
        assert len(stored) == 2
    finally:
        await engine.dispose()


async def test_failed_upsert_retries_and_web_replacement_hides_the_old_digest(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, Settings],
) -> None:
    store, settings = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    bodies = {
        "first": "The original article describes the first accepted digest.",
        "second": "The replacement article describes the newer accepted digest.",
    }
    current = "first"

    async def fetch_page(url: str) -> FetchedPage:
        assert url == "https://example.org/article"
        text = bodies[current]
        return FetchedPage(
            url=url,
            body=ARTICLE.format(title="Article", body=text).encode(),
        )

    service = IngestionService(
        factory,
        store,
        DeterministicEmbedder(),
        settings,
        fetch_page=fetch_page,
    )
    original_upsert = store.upsert

    async def fail_upsert(*args: object, **kwargs: object) -> None:
        raise provider_unavailable()

    store.upsert = fail_upsert  # type: ignore[method-assign]
    try:
        with pytest.raises(ResearchError) as exc_info:
            await service.ingest_web(research_id, "https://example.org/article")
        assert exc_info.value.code == "provider_unavailable"
        async with factory() as session:
            found = await session.scalars(
                select(Source).where(Source.research_id == research_id),
            )
            source = found.one()
        assert source.status is SourceStatus.FAILED
        assert source.content_hash is None
        store.upsert = original_upsert  # type: ignore[method-assign]
        indexed = await service.ingest_web(research_id, "https://example.org/article")
        current = "second"
        replaced = await service.ingest_web(research_id, "https://example.org/article")
        async with factory() as session:
            source = await session.get(Source, indexed.source_id)
        assert source is not None
        assert source.content_hash is not None
        assert replaced.source_id == indexed.source_id
        assert replaced.deduplicated is False
        visible = await store.search_accepted(
            research_id=research_id,
            vector=_vector(bodies["second"]),
            limit=5,
            versions=[
                AcceptedVersion(
                    source_id=indexed.source_id,
                    content_hash=source.content_hash,
                ),
            ],
        )
        stored = await store.search(
            research_id=research_id,
            vector=_vector(bodies["first"]),
            limit=10,
        )
        assert [match.url for match in visible] == ["https://example.org/article"]
        assert "replacement article" in visible[0].text
        assert visible[0].page_number is None
        assert all("original article" not in match.text for match in stored)
    finally:
        await engine.dispose()
