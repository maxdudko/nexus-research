"""Pre-indexed evidence stays in one research scope. The graph sends no PDF bytes."""

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from tests.conftest import make_settings
from tests.integration.conftest import require_local_qdrant
from tests.integration.scopes import quarantine_scopes
from tests.integration.test_ingestion import (
    APPLICATION_COLLECTION,
    ARTICLE,
    COLLECTION_PREFIX,
    WIDTH,
    DeterministicEmbedder,
    _pdf,
    _upgrade,
)
from tests.unit.graph_support import (
    NOW,
    QUESTION,
    FakeDiscovery,
    FakePlanner,
    ScriptedEvaluator,
    coverage,
    discovered,
    empty_discovery,
    plan_for,
)

from nexus.core.config import Settings, get_settings
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.ingestion.service import IngestionResult, IngestionService
from nexus.ingestion.transport import FetchedPage
from nexus.models.user import QUARANTINE_USER_ID
from nexus.research.budgets import limits_from_settings
from nexus.research.graph import GraphContext, build_graph
from nexus.research.nodes.synthesis import StaticSynthesizer
from nexus.research.runner import ResearchRunner
from nexus.retrieval.service import RetrievalService
from nexus.retrieval.vector_store import (
    EvidenceVectorStore,
    create_qdrant_client,
)

pytestmark = pytest.mark.integration

FOREIGN = "Foreign scope marker should stay invisible."


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


async def test_duplicate_ingest_stays_one_source_and_foreign_text_is_not_cited(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, Settings],
) -> None:
    store, settings = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    embedder = DeterministicEmbedder()
    page_url = "https://example.org/scope"

    async def fetch_page(url: str) -> FetchedPage:
        assert url == page_url
        return FetchedPage(
            url=url,
            body=ARTICLE.format(
                title="Scope",
                body="This page explains the accepted digest.",
            ).encode(),
        )

    service = IngestionService(
        factory,
        store,
        embedder,
        settings,
        fetch_page=fetch_page,
    )
    research_id = uuid.uuid4()
    other_id = uuid.uuid4()
    await quarantine_scopes(factory, research_id, other_id)
    document = _pdf(
        [
            "First page explains the indexing token.",
            "Second page explains the accepted digest.",
        ],
    )
    foreign = _pdf([FOREIGN])
    try:
        first_page = await service.ingest_web(
            research_id, page_url, owner_id=QUARANTINE_USER_ID
        )
        second_page = await service.ingest_web(
            research_id, page_url, owner_id=QUARANTINE_USER_ID
        )
        first_pdf = await service.ingest_pdf(
            research_id, document, "notes.pdf", owner_id=QUARANTINE_USER_ID
        )
        second_pdf = await service.ingest_pdf(
            research_id, document, "notes.pdf", owner_id=QUARANTINE_USER_ID
        )
        await service.ingest_pdf(
            other_id, foreign, "other.pdf", owner_id=QUARANTINE_USER_ID
        )
        assert second_page.deduplicated is True
        assert second_page.source_id == first_page.source_id
        assert second_pdf.deduplicated is True
        assert second_pdf.source_id == first_pdf.source_id

        context = GraphContext(
            planner=FakePlanner(plan_for(["accepted digest"])),
            discovery=FakeDiscovery([empty_discovery()]),
            ingestion=service,
            retrieval=RetrievalService(factory, store, embedder),
            evaluator=ScriptedEvaluator([coverage(sufficient=True)]),
            synthesizer=StaticSynthesizer(),
            limits=limits_from_settings(make_settings()),
            clock=lambda: NOW,
            owner_user_id=QUARANTINE_USER_ID,
        )
        runner = ResearchRunner(graph=build_graph(InMemorySaver()), context=context)
        result = await runner.run(QUESTION, research_id)
    finally:
        await engine.dispose()

    pages = {source.page_number for source in result.report.sources}
    encoded = result.report.model_dump_json()
    assert result.research_id == research_id
    assert result.status == "completed"
    assert 2 in pages
    assert all(
        source.research_id == str(research_id) for source in result.report.sources
    )
    assert FOREIGN not in encoded


class _RecordingIngestion(IngestionService):
    """The Phase 3 service, plus the results the graph received."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.results: list[IngestionResult] = []

    async def ingest_web(
        self,
        research_id: uuid.UUID,
        url: str,
        *,
        owner_id: uuid.UUID,
    ) -> IngestionResult:
        result = await super().ingest_web(research_id, url, owner_id=owner_id)
        self.results.append(result)
        return result


async def test_a_second_graph_run_refetches_and_keeps_one_source(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, Settings],
) -> None:
    store, settings = evidence_store
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    embedder = DeterministicEmbedder()
    page_url = "https://example.org/repeat"
    fetches: list[str] = []

    async def fetch_page(url: str) -> FetchedPage:
        fetches.append(url)
        return FetchedPage(
            url=url,
            body=ARTICLE.format(
                title="Repeat",
                body="This page explains the accepted digest.",
            ).encode(),
        )

    service = _RecordingIngestion(
        factory,
        store,
        embedder,
        settings,
        fetch_page=fetch_page,
    )
    page = discovered(page_url, search_calls=1, llm_calls=1)
    context = GraphContext(
        planner=FakePlanner(plan_for(["accepted digest"])),
        discovery=FakeDiscovery([page, page]),
        ingestion=service,
        retrieval=RetrievalService(factory, store, embedder),
        evaluator=ScriptedEvaluator(
            [coverage(sufficient=True), coverage(sufficient=True)]
        ),
        synthesizer=StaticSynthesizer(),
        limits=limits_from_settings(make_settings()),
        clock=lambda: NOW,
        owner_user_id=QUARANTINE_USER_ID,
    )
    runner = ResearchRunner(graph=build_graph(InMemorySaver()), context=context)
    research_id = uuid.uuid4()
    await quarantine_scopes(factory, research_id)
    try:
        first = await runner.run(QUESTION, research_id)
        second = await runner.run(QUESTION, first.research_id)
    finally:
        await engine.dispose()

    assert second.research_id == first.research_id
    assert second.run_id != first.run_id
    assert fetches == [page_url, page_url]
    assert [item.deduplicated for item in service.results] == [False, True]
    assert service.results[0].source_id == service.results[1].source_id
