"""Evidence routes stay thin and use the shared error envelope."""

import uuid
from collections.abc import AsyncIterator

import pytest
from tests.conftest import client_for, make_settings, signed_in

from nexus.ai.errors import provider_timeout, provider_unavailable
from nexus.api.v1.evidence import get_ingestion, get_retrieval
from nexus.ingestion.service import IngestionResult
from nexus.main import create_app
from nexus.retrieval.vector_store import EvidenceMatch

RESEARCH_ID = uuid.UUID("a8c11b5e-fd31-4c24-9d80-132b317fa7f5")

pytestmark = pytest.mark.usefixtures("stub_scopes")


class FakeIngestion:
    """Records the route inputs and returns one indexed source."""

    def __init__(self) -> None:
        self.pdf: tuple[uuid.UUID, bytes, str | None] | None = None
        self.web: tuple[uuid.UUID, str] | None = None
        self.settings = make_settings()

    async def ingest_pdf(
        self,
        research_id: uuid.UUID,
        data: bytes,
        display_name: str | None,
        *,
        owner_id: uuid.UUID,
    ) -> IngestionResult:
        del owner_id
        self.pdf = (research_id, data, display_name)
        return IngestionResult(
            source_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            research_id=research_id,
            status="indexed",
            chunk_count=2,
            deduplicated=False,
        )

    async def ingest_web(
        self,
        research_id: uuid.UUID,
        url: str,
        *,
        owner_id: uuid.UUID,
    ) -> IngestionResult:
        del owner_id
        self.web = (research_id, url)
        return IngestionResult(
            source_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
            research_id=research_id,
            status="indexed",
            chunk_count=1,
            deduplicated=True,
        )


async def test_pdf_route_returns_the_ingestion_contract() -> None:
    app = signed_in(create_app(make_settings()))
    fake = FakeIngestion()

    async def override() -> AsyncIterator[FakeIngestion]:
        yield fake

    app.dependency_overrides[get_ingestion] = override
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/evidence/pdf",
            data={"research_id": str(RESEARCH_ID)},
            files={"file": ("folder/notes.pdf", b"%PDF-1.7 tiny", "application/pdf")},
        )
        live = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "source_id": "11111111-1111-4111-8111-111111111111",
        "research_id": str(RESEARCH_ID),
        "status": "indexed",
        "chunk_count": 2,
        "deduplicated": False,
    }
    assert fake.pdf == (RESEARCH_ID, b"%PDF-1.7 tiny", "notes.pdf")
    assert live.status_code == 200


async def test_unsafe_web_url_is_rejected_before_indexing() -> None:
    app = signed_in(create_app(make_settings()))
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/evidence/web",
            json={
                "research_id": str(RESEARCH_ID),
                "url": "http://user:secret@127.0.0.1/admin",
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_url"
    assert "secret" not in response.text
    assert "127.0.0.1" not in response.text


class FakeRetrieval:
    """Records a search and returns one passage."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[uuid.UUID, str, int]] = []

    async def search(
        self,
        research_id: uuid.UUID,
        query: str,
        top_k: int,
        *,
        owner_id: uuid.UUID,
    ) -> list[EvidenceMatch]:
        del owner_id
        self.calls.append((research_id, query, top_k))
        if self.error is not None:
            raise self.error
        return [
            EvidenceMatch(
                chunk_id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
                source_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
                research_id=research_id,
                score=0.82,
                text="Example retrieved passage, not a model-generated answer.",
                page_number=12,
                url=None,
                title="Hidden title",
                content_hash="ab" * 32,
                chunk_index=4,
            ),
        ]


async def test_search_route_returns_evidence_without_an_answer() -> None:
    app = signed_in(create_app(make_settings()))
    fake = FakeRetrieval()

    async def override() -> AsyncIterator[FakeRetrieval]:
        yield fake

    app.dependency_overrides[get_retrieval] = override
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/evidence/search",
            json={"research_id": str(RESEARCH_ID), "query": "  agent limits  "},
        )
        plan = await client.post(
            "/api/v1/research/plan",
            json={"question": "How do agents plan work?"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "matches": [
            {
                "chunk_id": "33333333-3333-4333-8333-333333333333",
                "source_id": "11111111-1111-4111-8111-111111111111",
                "research_id": str(RESEARCH_ID),
                "score": 0.82,
                "text": "Example retrieved passage, not a model-generated answer.",
                "page_number": 12,
                "url": None,
            },
        ],
    }
    assert "answer" not in response.json()
    assert "content_hash" not in response.text
    assert "Hidden title" not in response.text
    assert fake.calls == [(RESEARCH_ID, "agent limits", 5)]
    assert plan.status_code == 503


@pytest.mark.parametrize(
    "payload",
    [
        {"research_id": str(RESEARCH_ID), "query": "   "},
        {
            "research_id": str(RESEARCH_ID),
            "query": "secret-token " + ("x" * 2000),
        },
        {
            "research_id": str(RESEARCH_ID),
            "query": "secret-token",
            "top_k": 0,
        },
        {
            "research_id": str(RESEARCH_ID),
            "query": "secret-token",
            "top_k": 11,
        },
    ],
)
async def test_search_validation_names_the_field_only(
    payload: dict[str, object],
) -> None:
    app = signed_in(create_app(make_settings()))
    async with client_for(app) as client:
        response = await client.post("/api/v1/evidence/search", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "secret-token" not in response.text


@pytest.mark.parametrize(
    ("error", "status"),
    [(provider_unavailable(), 503), (provider_timeout(), 504)],
)
async def test_search_provider_errors_use_the_envelope(
    error: Exception,
    status: int,
) -> None:
    app = signed_in(create_app(make_settings()))

    async def override() -> AsyncIterator[FakeRetrieval]:
        yield FakeRetrieval(error=error)

    app.dependency_overrides[get_retrieval] = override
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/evidence/search",
            json={"research_id": str(RESEARCH_ID), "query": "agent limits"},
        )
    assert response.status_code == status
    body = response.json()["error"]
    assert body["code"] == error.code
    assert "agent limits" not in response.text
