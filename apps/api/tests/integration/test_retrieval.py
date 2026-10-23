"""Scoped search against local PostgreSQL and Qdrant."""

import hashlib
import uuid
from collections.abc import Sequence

import pymupdf
import pytest
from tests.conftest import make_settings
from tests.integration.conftest import require_local_qdrant, run_alembic
from tests.integration.scopes import quarantine_scopes

from nexus.ai.errors import ResearchError
from nexus.core.config import Settings, get_settings
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.ingestion.service import IngestionService
from nexus.ingestion.transport import FetchedPage
from nexus.models.user import QUARANTINE_USER_ID
from nexus.retrieval.service import RetrievalService
from nexus.retrieval.vector_store import (
    EvidenceVectorStore,
    create_qdrant_client,
)

pytestmark = pytest.mark.integration

WIDTH = 8
COLLECTION_PREFIX = "nexus_evidence_it_"
APPLICATION_COLLECTION = "nexus_evidence_v1"
PAGE_ONE = "First page explains the indexing token."
PAGE_TWO = "Second page explains the accepted digest."
ORIGINAL = "The original article describes the first accepted digest."
REPLACEMENT = "The replacement article describes the newer accepted digest."
ARTICLE = """<!DOCTYPE html><html><head><title>Article</title></head>
<body><article><h1>Article</h1><p>{body}</p></article></body></html>"""
WEB_URL = "https://example.org/article"


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


async def test_search_is_scoped_and_hides_a_replaced_web_digest(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, Settings],
) -> None:
    store, settings = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    embedder = DeterministicEmbedder()
    research_a = uuid.uuid4()
    research_b = uuid.uuid4()
    await quarantine_scopes(factory, research_a, research_b)
    bodies = {research_a: ORIGINAL, research_b: ORIGINAL}
    current = research_a

    async def fetch_current(url: str) -> FetchedPage:
        assert url == WEB_URL
        return FetchedPage(
            url=url,
            body=ARTICLE.format(body=bodies[current]).encode(),
        )

    ingestion = IngestionService(
        factory,
        store,
        embedder,
        settings,
        fetch_page=fetch_current,
    )
    search = RetrievalService(factory, store, embedder)
    pdf = _pdf([PAGE_ONE, PAGE_TWO])
    try:
        await ingestion.ingest_pdf(
            research_a, pdf, "notes.pdf", owner_id=QUARANTINE_USER_ID
        )
        await ingestion.ingest_pdf(
            research_b, pdf, "notes.pdf", owner_id=QUARANTINE_USER_ID
        )
        await ingestion.ingest_web(research_a, WEB_URL, owner_id=QUARANTINE_USER_ID)
        current = research_b
        await ingestion.ingest_web(research_b, WEB_URL, owner_id=QUARANTINE_USER_ID)
        first_a = await search.search(
            research_a, PAGE_ONE, 5, owner_id=QUARANTINE_USER_ID
        )
        first_b = await search.search(
            research_b, PAGE_ONE, 5, owner_id=QUARANTINE_USER_ID
        )
        with pytest.raises(ResearchError) as missing:
            await search.search(uuid.uuid4(), PAGE_ONE, 5, owner_id=QUARANTINE_USER_ID)

        pages = {
            match.page_number: match.text for match in first_a if match.url is None
        }
        assert pages[1].startswith("First page")
        assert "Second page" in pages[2]
        assert {match.research_id for match in first_a} == {research_a}
        assert {match.research_id for match in first_b} == {research_b}
        assert {match.source_id for match in first_a}.isdisjoint(
            {match.source_id for match in first_b},
        )
        web_a = [match for match in first_a if match.url == WEB_URL]
        web_b = [match for match in first_b if match.url == WEB_URL]
        assert web_a[0].page_number is None
        assert "original article" in web_a[0].text
        assert "original article" in web_b[0].text
        assert missing.value.code == "not_found"

        bodies[research_a] = REPLACEMENT
        current = research_a
        await ingestion.ingest_web(research_a, WEB_URL, owner_id=QUARANTINE_USER_ID)
        replaced = await search.search(
            research_a, REPLACEMENT, 5, owner_id=QUARANTINE_USER_ID
        )
        untouched = await search.search(
            research_b, ORIGINAL, 5, owner_id=QUARANTINE_USER_ID
        )
        replaced_web = [match for match in replaced if match.url == WEB_URL]
        untouched_web = [match for match in untouched if match.url == WEB_URL]
        assert "replacement article" in replaced_web[0].text
        assert all("original article" not in match.text for match in replaced)
        assert "original article" in untouched_web[0].text
    finally:
        await engine.dispose()
