"""One account cannot read or spend another account's research scope."""

import asyncio
from io import StringIO
from uuid import UUID

import pytest
from sqlalchemy import func, select
from tests.conftest import client_for, make_settings
from tests.integration.conftest import run_alembic
from tests.integration.test_ingestion import DeterministicEmbedder, _pdf
from tests.integration.test_research_stream_integration import _runner, parse_sse
from tests.unit.graph_support import QUESTION

from nexus.ai.errors import ResearchError
from nexus.api.v1.research import get_runner
from nexus.identity.cli import run_cli
from nexus.identity.store import get_user_by_email
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.ingestion.service import IngestionService
from nexus.main import create_app
from nexus.models.run import ResearchRun
from nexus.models.source import Source
from nexus.research.runner import ResearchRunner
from nexus.retrieval.service import RetrievalService
from nexus.retrieval.vector_store import EvidenceVectorStore

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.test_ingestion",)

PASSWORD = "correct-horse-1"
ORIGIN = {"origin": "http://127.0.0.1:3000"}
NOT_FOUND = {
    "error": {
        "code": "not_found",
        "message": "The research run was not found.",
    }
}


async def _user(database_url: str, email: str) -> None:
    code = await asyncio.to_thread(
        run_cli,
        ["create-user", email],
        stdin=StringIO(PASSWORD + "\n"),
        stderr=StringIO(),
        settings=make_settings(database_url=database_url),
    )
    assert code == 0


async def _login(client, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    token = client.cookies.get("nexus_csrf")
    assert token
    return {**ORIGIN, "x-csrf-token": token}


def _run_id(payload: str) -> str:
    frames = parse_sse(payload)
    completed = frames[-1][1]
    assert completed is not None
    return str(completed["run_id"])


async def test_a_foreign_scope_is_invisible_and_an_owned_scope_still_works(
    test_database_url: str,
    evidence_store: tuple[EvidenceVectorStore, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upgraded = await run_alembic(test_database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    await _user(test_database_url, "ada@example.com")
    await _user(test_database_url, "bea@example.com")
    store, service_settings = evidence_store
    app = create_app(make_settings(database_url=test_database_url))

    async def override() -> ResearchRunner:
        return _runner()

    app.dependency_overrides[get_runner] = override
    writes: list[str] = []

    async def reject_upsert(
        self: EvidenceVectorStore,
        *args: object,
        **kwargs: object,
    ) -> None:
        del self, args, kwargs
        writes.append("upsert")

    async def reject_search(
        self: EvidenceVectorStore,
        *args: object,
        **kwargs: object,
    ) -> list[object]:
        del self, args, kwargs
        writes.append("search")
        return []

    monkeypatch.setattr(EvidenceVectorStore, "upsert", reject_upsert)
    monkeypatch.setattr(EvidenceVectorStore, "search", reject_search)

    async with client_for(app) as ada, client_for(app) as bea:
        ada_headers = await _login(ada, "ada@example.com")
        bea_headers = await _login(bea, "bea@example.com")
        minted = await ada.post("/api/v1/research/scopes", headers=ada_headers)
        assert minted.status_code == 200
        research_id = str(minted.json()["research_id"])
        foreign = {"question": QUESTION, "research_id": research_id}
        pdf = await bea.post(
            "/api/v1/evidence/pdf",
            data={"research_id": research_id},
            files={"file": ("notes.pdf", b"%PDF-1.7", "application/pdf")},
            headers=bea_headers,
        )
        web = await bea.post(
            "/api/v1/evidence/web",
            json={"research_id": research_id, "url": "https://example.org/article"},
            headers=bea_headers,
        )
        search = await bea.post(
            "/api/v1/evidence/search",
            json={"research_id": research_id, "query": "accepted digest"},
            headers=bea_headers,
        )
        denied = await bea.post(
            "/api/v1/research/run", json=foreign, headers=bea_headers
        )
        first, second = await asyncio.gather(
            bea.post("/api/v1/research/stream", json=foreign, headers=bea_headers),
            bea.post("/api/v1/research/stream", json=foreign, headers=bea_headers),
        )
        assert writes == []
        monkeypatch.undo()

        first_run = await ada.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
            headers=ada_headers,
        )
        second_run = await ada.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
            headers=ada_headers,
        )
        assert first_run.status_code == second_run.status_code == 200
        run_id = _run_id(first_run.text)
        detail = await bea.get(f"/api/v1/research/runs/{run_id}")
        page = await ada.get(
            "/api/v1/research/runs",
            params={"limit": 1},
            headers=ada_headers,
        )
        cursor = page.json()["next_cursor"]
        assert cursor
        hidden = await bea.get(
            "/api/v1/research/runs",
            params={"limit": 20, "cursor": cursor},
        )
        own_detail = await ada.get(
            f"/api/v1/research/runs/{run_id}",
            headers=ada_headers,
        )

    assert pdf.status_code == web.status_code == search.status_code == 404
    assert denied.status_code == first.status_code == second.status_code == 404
    assert pdf.json() == NOT_FOUND
    assert research_id not in pdf.text
    assert detail.status_code == 404
    assert detail.json() == NOT_FOUND
    assert QUESTION not in detail.text
    assert hidden.status_code == 200
    assert QUESTION not in hidden.text
    assert own_detail.status_code == 200
    assert own_detail.json()["run"]["question"] == QUESTION

    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    try:
        scope_id = UUID(research_id)
        async with factory() as session:
            sources = await session.scalar(select(func.count()).select_from(Source))
            foreign_runs = await session.scalar(
                select(func.count())
                .select_from(ResearchRun)
                .where(ResearchRun.research_id == scope_id)
            )
        assert sources == 0
        assert foreign_runs == 0
        ada_account = await get_user_by_email(factory, "ada@example.com")
        bea_account = await get_user_by_email(factory, "bea@example.com")
        assert ada_account is not None and bea_account is not None
        ingestion = IngestionService(
            factory,
            store,
            DeterministicEmbedder(),
            service_settings,  # type: ignore[arg-type]
        )
        retrieval = RetrievalService(factory, store, DeterministicEmbedder())
        document = _pdf(["Accepted digest stays on this page."])
        with pytest.raises(ResearchError) as missing:
            await ingestion.ingest_pdf(
                scope_id,
                document,
                "notes.pdf",
                owner_id=bea_account.id,
            )
        assert missing.value.code == "not_found"
        indexed = await ingestion.ingest_pdf(
            scope_id,
            document,
            "notes.pdf",
            owner_id=ada_account.id,
        )
        again = await ingestion.ingest_pdf(
            scope_id,
            document,
            "notes.pdf",
            owner_id=ada_account.id,
        )
        matches = await retrieval.search(
            scope_id,
            "Accepted digest",
            5,
            owner_id=ada_account.id,
        )
        assert again.deduplicated is True
        assert again.source_id == indexed.source_id
        assert matches
        assert "Accepted digest" in matches[0].text
        async with factory() as session:
            sources_after = await session.scalar(
                select(func.count()).select_from(Source)
            )
        assert sources_after == 1
    finally:
        await engine.dispose()
