"""Planning routes stay independent of health and hide provider details."""

from langchain_core.messages import AIMessage
from tests.conftest import client_for, make_settings

from nexus.ai.planner import Planner
from nexus.api.v1.health import ReadyStatus, get_ready_status
from nexus.api.v1.research import get_discovery, get_planner
from nexus.main import create_app
from nexus.research.discovery import Discovery
from nexus.schemas.research import ResearchPlan, ResearchTask, SearchResult

QUESTION = "How do AI coding agents improve software development workflows?"
SECRET = "sk-route-secret"


class FakeModel:
    def __init__(self, result: object) -> None:
        self._result = result

    async def ainvoke(self, messages: list[object]) -> object:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _plan() -> ResearchPlan:
    return ResearchPlan(
        objective="Understand agents in software development",
        tasks=[
            ResearchTask(
                question="Which activities benefit from AI agents?",
                search_queries=["AI coding agents software development workflows"],
            )
        ],
    )


async def test_plan_returns_the_contract_and_live_stays_ok() -> None:
    app = create_app(make_settings(openai_api_key=SECRET))
    app.dependency_overrides[get_planner] = lambda: Planner(
        app.state.settings,
        model=FakeModel(_plan()),
    )

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/plan",
            json={"question": f"  {QUESTION}  ", "ignored": True},
        )
        live = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "objective": "Understand agents in software development",
        "tasks": [
            {
                "question": "Which activities benefit from AI agents?",
                "search_queries": ["AI coding agents software development workflows"],
            }
        ],
    }
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


async def test_invalid_question_is_422_and_does_not_echo_the_body() -> None:
    app = create_app(make_settings())
    question = "  sk-echoed  "

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/plan",
            json={"question": question},
        )

    body = response.json()
    assert response.status_code == 422
    assert set(body) == {"error"}
    assert body["error"]["code"] == "invalid_request"
    assert "question" in body["error"]["message"]
    assert question not in response.text


async def test_missing_key_is_503_and_live_stays_ok() -> None:
    app = create_app(make_settings(openai_api_key=None))

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/plan",
            json={"question": QUESTION},
        )
        live = await client.get("/api/v1/health/live")

    body = response.json()
    assert response.status_code == 503
    assert body == {
        "error": {
            "code": "provider_not_configured",
            "message": "Research provider is not configured.",
        }
    }
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


async def test_provider_failure_does_not_leak_the_secret() -> None:
    app = create_app(make_settings(openai_api_key=SECRET))
    app.dependency_overrides[get_planner] = lambda: Planner(
        app.state.settings,
        model=FakeModel(RuntimeError(f"{SECRET} planner prompt")),
    )

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/plan",
            json={"question": QUESTION},
        )

    body = response.json()
    assert response.status_code == 503
    assert body["error"]["code"] == "provider_unavailable"
    assert SECRET not in response.text
    assert "planner prompt" not in response.text
    assert QUESTION not in response.text


class _RouteSearch:
    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        assert query == "AI agents"
        assert max_results == 5
        return [
            SearchResult(
                title="Notes",
                url="https://example.org/notes",
                snippet="The notes describe code review.",
            )
        ]


class _RouteModel:
    def __init__(self) -> None:
        self._replies = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "AI agents"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The notes describe code review."),
        ]

    async def ainvoke(self, _messages: list[object]) -> AIMessage:
        return self._replies.pop(0)


async def test_discover_returns_the_contract_and_live_stays_ok() -> None:
    app = create_app(make_settings(openai_api_key=SECRET, tavily_api_key="tvly-test"))
    app.dependency_overrides[get_discovery] = lambda: Discovery(
        app.state.settings,
        model=_RouteModel(),
        search=_RouteSearch(),
    )

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/discover",
            json={"question": QUESTION},
        )
        live = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["summary"] == "The notes describe code review."
    assert body["search_calls"] == 1
    assert body["llm_calls"] == 2
    assert body["warnings"] == []
    assert body["sources"] == [
        {
            "title": "Notes",
            "url": "https://example.org/notes",
            "snippet": "The notes describe code review.",
            "published_at": None,
        }
    ]
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


async def test_discover_without_tavily_key_is_503_and_live_stays_ok() -> None:
    app = create_app(make_settings(openai_api_key=SECRET, tavily_api_key=None))

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/discover",
            json={"question": QUESTION},
        )
        live = await client.get("/api/v1/health/live")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_not_configured"
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


async def test_discover_without_openai_key_is_503_and_ready_stays_ok() -> None:
    app = create_app(make_settings(openai_api_key=None, tavily_api_key="tvly-test"))

    async def ready_override() -> ReadyStatus:
        return ReadyStatus(status="ok", postgres="ok", qdrant="ok")

    app.dependency_overrides[get_ready_status] = ready_override

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/research/discover",
            json={"question": QUESTION},
        )
        ready = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_not_configured"
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "postgres": "ok", "qdrant": "ok"}
