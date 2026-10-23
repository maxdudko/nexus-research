"""Opt-in live smoke for one real run. Default pytest and CI do not collect it."""

import os
import uuid

import pytest
from tests.integration.scopes import quarantine_scopes
from tests.integration.test_ingestion import _pdf

from nexus.core.config import get_settings
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.ingestion.service import IngestionService
from nexus.models.user import QUARANTINE_USER_ID
from nexus.research.checkpoint import open_checkpointer, setup_checkpoint
from nexus.research.runner import ResearchRunner, production_context
from nexus.retrieval.embeddings import OpenAIEmbedder
from nexus.retrieval.vector_store import EvidenceVectorStore

pytestmark = [pytest.mark.live, pytest.mark.integration]

QUESTION = "How do bounded research graphs stop after a deadline?"
PAGE_SENTENCE = "The bounded graph citation token appears only on this second page."


def _require_live() -> None:
    if os.environ.get("NEXUS_RUN_LIVE_AI") != "1":
        pytest.skip("set NEXUS_RUN_LIVE_AI=1 to run paid provider smoke tests")


async def test_live_runner_returns_a_report() -> None:
    _require_live()
    settings = get_settings()
    if not settings.openai_api_key or not settings.tavily_api_key:
        pytest.skip("OpenAI and Tavily keys are required")
    database_url = str(settings.database_url)
    await setup_checkpoint(database_url)
    runtime = await open_checkpointer(database_url)
    store = EvidenceVectorStore(settings)
    engine = create_db_engine(database_url)
    factory = session_factory(engine)
    try:
        runner = ResearchRunner(
            graph=runtime.graph,
            context=production_context(
                settings, factory, store, owner_user_id=QUARANTINE_USER_ID
            ),
        )
        result = await runner.run(QUESTION, None)
    finally:
        await store.close()
        await runtime.pool.close()
        await engine.dispose()

    assert result.status in {"completed", "partial", "insufficient_evidence"}
    assert result.report.status == result.status
    assert result.run_id != result.research_id


async def test_live_preindexed_pdf_can_be_cited_by_page() -> None:
    _require_live()
    settings = get_settings()
    if not settings.openai_api_key:
        pytest.skip("NEXUS_OPENAI_API_KEY is required")
    database_url = str(settings.database_url)
    await setup_checkpoint(database_url)
    runtime = await open_checkpointer(database_url)
    store = EvidenceVectorStore(settings)
    engine = create_db_engine(database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    await quarantine_scopes(factory, research_id)
    document = _pdf(["First page is only context.", PAGE_SENTENCE])
    try:
        indexed = await IngestionService(
            factory,
            store,
            OpenAIEmbedder(settings),
            settings,
        ).ingest_pdf(research_id, document, "notes.pdf", owner_id=QUARANTINE_USER_ID)
        runner = ResearchRunner(
            graph=runtime.graph,
            context=production_context(
                settings, factory, store, owner_user_id=QUARANTINE_USER_ID
            ),
        )
        result = await runner.run(
            "What does the second page say about the bounded graph citation token?",
            research_id,
        )
    finally:
        await store.close()
        await runtime.pool.close()
        await engine.dispose()

    pages = {source.page_number for source in result.report.sources}
    assert indexed.chunk_count >= 1
    assert 2 in pages
    assert PAGE_SENTENCE in result.report.model_dump_json()
