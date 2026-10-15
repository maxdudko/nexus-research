"""Planning routes stay independent of health and hide provider details."""

from tests.conftest import client_for, make_settings

from nexus.ai.planner import Planner
from nexus.api.v1.research import get_planner
from nexus.main import create_app
from nexus.schemas.research import ResearchPlan, ResearchTask

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
