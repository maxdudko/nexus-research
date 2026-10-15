"""Opt-in OpenAI smoke test. Default pytest and CI do not collect it."""

import os

import pytest
from tests.conftest import make_settings

from nexus.ai.planner import Planner
from nexus.research.discovery import Discovery
from nexus.schemas.research import ResearchPlan, SearchResult
from nexus.tools.web_search import WebSearch

pytestmark = [pytest.mark.live, pytest.mark.integration]


async def test_live_planner_returns_research_plan() -> None:
    if os.environ.get("NEXUS_RUN_LIVE_AI") != "1":
        pytest.skip("set NEXUS_RUN_LIVE_AI=1 to run paid provider smoke tests")
    key = os.environ.get("NEXUS_OPENAI_API_KEY")
    if not key:
        pytest.skip("NEXUS_OPENAI_API_KEY is required")

    settings = make_settings(
        openai_api_key=key,
        openai_model=os.environ.get("NEXUS_OPENAI_MODEL", "gpt-4o-mini"),
    )
    plan = await Planner(settings).create_plan(
        "How do AI coding agents improve software development workflows?",
    )

    assert isinstance(plan, ResearchPlan)
    assert plan.objective.strip()
    assert plan.tasks


async def test_live_tavily_search_returns_http_urls() -> None:
    if os.environ.get("NEXUS_RUN_LIVE_AI") != "1":
        pytest.skip("set NEXUS_RUN_LIVE_AI=1 to run paid provider smoke tests")
    key = os.environ.get("NEXUS_TAVILY_API_KEY")
    if not key:
        pytest.skip("NEXUS_TAVILY_API_KEY is required")

    results = await WebSearch(make_settings(tavily_api_key=key)).search(
        "OpenAI",
        max_results=1,
    )

    assert isinstance(results, list)
    for result in results:
        assert isinstance(result, SearchResult)
        assert result.url.startswith("https://") or result.url.startswith("http://")


async def test_live_discover_requests_web_search() -> None:
    if os.environ.get("NEXUS_RUN_LIVE_AI") != "1":
        pytest.skip("set NEXUS_RUN_LIVE_AI=1 to run paid provider smoke tests")
    openai_key = os.environ.get("NEXUS_OPENAI_API_KEY")
    tavily_key = os.environ.get("NEXUS_TAVILY_API_KEY")
    if not openai_key or not tavily_key:
        pytest.skip("NEXUS_OPENAI_API_KEY and NEXUS_TAVILY_API_KEY are required")

    result = await Discovery(
        make_settings(
            openai_api_key=openai_key,
            tavily_api_key=tavily_key,
            openai_model=os.environ.get("NEXUS_OPENAI_MODEL", "gpt-4o-mini"),
        )
    ).discover("How do AI coding agents improve software development workflows?")

    assert 1 <= result.search_calls <= 3
    assert 1 <= result.llm_calls <= 4
    assert result.sources
    assert result.status in {"completed", "partial"}
