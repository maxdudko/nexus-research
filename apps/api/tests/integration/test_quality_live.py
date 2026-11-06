"""Opt-in paid end-to-end quality smoke using isolated local storage."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from tests.integration.conftest import require_local_qdrant, run_alembic
from tests.integration.scopes import quarantine_scopes

from nexus.ai.client import StructuredOutputModel
from nexus.ai.synthesis import OpenAIReportSynthesizer
from nexus.core.config import get_settings
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.models.user import QUARANTINE_USER_ID
from nexus.observability.context import bind_run
from nexus.research.graph import build_graph
from nexus.research.runner import ResearchRunner, production_context
from nexus.retrieval.vector_store import EvidenceVectorStore, create_qdrant_client
from nexus.schemas.report import ModelReportV2

pytestmark = [pytest.mark.integration, pytest.mark.live]


async def test_quality_pipeline_live(test_database_url: str) -> None:
    if os.environ.get("NEXUS_RUN_LIVE_AI") != "1":
        pytest.skip("requires NEXUS_RUN_LIVE_AI=1")
    configured = get_settings()
    if not configured.openai_api_key or not configured.tavily_api_key:
        pytest.skip("requires OpenAI and Tavily keys")
    require_local_qdrant(str(configured.qdrant_url))
    migration = await run_alembic(test_database_url, "upgrade", "head")
    assert migration.returncode == 0, migration.stderr
    name = f"nexus_quality_smoke_{uuid4().hex}"
    settings = configured.model_copy(
        update={"database_url": test_database_url, "rag_collection": name}
    )
    engine = create_db_engine(test_database_url)
    sessions = session_factory(engine)
    client = create_qdrant_client(settings)
    store = EvidenceVectorStore(settings, client)
    scope = uuid4()
    await quarantine_scopes(sessions, scope)
    try:
        drafts = []

        class RecordingWriter:
            async def ainvoke(self, messages):
                model = StructuredOutputModel(
                    settings.model_copy(
                        update={
                            "ai_timeout_seconds": 70,
                            "ai_max_retries": 0,
                        }
                    ),
                    ModelReportV2,
                )
                result = await model.ainvoke(messages)
                drafts.append(result.model_dump(mode="json"))
                return result

        context = production_context(
            settings,
            sessions,
            store,
            owner_user_id=QUARANTINE_USER_ID,
        )
        context.synthesizer = OpenAIReportSynthesizer(settings, RecordingWriter())
        runner = ResearchRunner(graph=build_graph(), context=context)
        with bind_run(create_buffer=True) as usage:
            result = await runner.run(
                "How does PostgreSQL VACUUM reclaim dead tuples, and what are its "
                "limitations? Use official PostgreSQL documentation.",
                scope,
            )
            artifact = {
                "kind": "paid_smoke_not_quality_benchmark",
                "drafts": drafts,
                "result": result.model_dump(mode="json"),
                "operations": [r.model_dump(mode="json") for r in usage.records]
                if usage
                else [],
            }
        await asyncio.to_thread(
            Path("/tmp/nexus-quality-live-smoke.json").write_text,
            json.dumps(artifact, indent=2),
        )
        assert result.report.format_version == 2
        assert result.report.sources
        assert result.report.verification in {"passed", "incomplete"}
        assert result.usage.llm_calls <= 20
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()
        await engine.dispose()
