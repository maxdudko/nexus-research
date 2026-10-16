"""Evidence routes stay thin and use the shared error envelope."""

import uuid
from collections.abc import AsyncIterator

from tests.conftest import client_for, make_settings

from nexus.api.v1.evidence import get_ingestion
from nexus.ingestion.service import IngestionResult
from nexus.main import create_app

RESEARCH_ID = uuid.UUID("a8c11b5e-fd31-4c24-9d80-132b317fa7f5")


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
    ) -> IngestionResult:
        self.pdf = (research_id, data, display_name)
        return IngestionResult(
            source_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            research_id=research_id,
            status="indexed",
            chunk_count=2,
            deduplicated=False,
        )

    async def ingest_web(self, research_id: uuid.UUID, url: str) -> IngestionResult:
        self.web = (research_id, url)
        return IngestionResult(
            source_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
            research_id=research_id,
            status="indexed",
            chunk_count=1,
            deduplicated=True,
        )


async def test_pdf_route_returns_the_ingestion_contract() -> None:
    app = create_app(make_settings())
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
    app = create_app(make_settings())
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
