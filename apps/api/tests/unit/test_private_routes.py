"""Anonymous callers stop before a private route opens research services."""

import pytest
from tests.conftest import client_for, make_settings

from nexus.api.v1.evidence import get_ingestion, get_retrieval, get_web_ingestion
from nexus.api.v1.research import get_discovery, get_planner, get_runner
from nexus.main import create_app

QUESTION = "How do AI coding agents improve software development workflows?"
RESEARCH = "a8c11b5e-fd31-4c24-9d80-132b317fa7f5"
UNAUTHENTICATED = {
    "error": {
        "code": "unauthenticated",
        "message": "Authentication is required.",
    }
}


@pytest.mark.parametrize(
    ("method", "path", "json_body", "files"),
    [
        ("post", "/api/v1/research/plan", {"question": "short"}, None),
        ("post", "/api/v1/research/discover", {"question": QUESTION}, None),
        ("post", "/api/v1/research/run", {"question": QUESTION}, None),
        ("post", "/api/v1/research/stream", {"question": QUESTION}, None),
        ("post", "/api/v1/research/scopes", None, None),
        ("get", "/api/v1/research/runs", None, None),
        (
            "get",
            f"/api/v1/research/runs/{RESEARCH}",
            None,
            None,
        ),
        (
            "post",
            "/api/v1/evidence/web",
            {"research_id": RESEARCH, "url": "https://example.com/a"},
            None,
        ),
        (
            "post",
            "/api/v1/evidence/search",
            {"research_id": RESEARCH, "query": "agent limits"},
            None,
        ),
    ],
)
async def test_anonymous_json_routes_do_not_call_services(
    method: str,
    path: str,
    json_body: dict[str, str] | None,
    files: object,
) -> None:
    del files
    app = create_app(make_settings())
    called: list[str] = []

    def planner() -> object:
        called.append("planner")
        raise AssertionError("planner")

    def discovery() -> object:
        called.append("discovery")
        raise AssertionError("discovery")

    async def runner() -> object:
        called.append("runner")
        raise AssertionError("runner")

    async def ingestion() -> object:
        called.append("ingestion")
        raise AssertionError("ingestion")

    async def retrieval() -> object:
        called.append("retrieval")
        raise AssertionError("retrieval")

    app.dependency_overrides[get_planner] = planner
    app.dependency_overrides[get_discovery] = discovery
    app.dependency_overrides[get_runner] = runner
    app.dependency_overrides[get_ingestion] = ingestion
    app.dependency_overrides[get_web_ingestion] = ingestion
    app.dependency_overrides[get_retrieval] = retrieval
    async with client_for(app) as client:
        response = await client.request(method, path, json=json_body)
    assert response.status_code == 401
    assert response.json() == UNAUTHENTICATED
    assert called == []


async def test_anonymous_pdf_does_not_open_ingestion() -> None:
    app = create_app(make_settings())
    called: list[str] = []

    async def ingestion() -> object:
        called.append("ingestion")
        raise AssertionError("ingestion")

    app.dependency_overrides[get_ingestion] = ingestion
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/evidence/pdf",
            data={"research_id": RESEARCH},
            files={"file": ("notes.pdf", b"%PDF-1.7", "application/pdf")},
        )
    assert response.status_code == 401
    assert response.json() == UNAUTHENTICATED
    assert called == []


async def test_health_stays_anonymous() -> None:
    app = create_app(make_settings())
    async with client_for(app) as client:
        response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
