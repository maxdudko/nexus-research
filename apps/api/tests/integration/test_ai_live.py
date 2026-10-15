"""Opt-in OpenAI smoke test. Default pytest and CI do not collect it."""

import os

import pytest
from tests.conftest import make_settings

from nexus.ai.planner import Planner
from nexus.schemas.research import ResearchPlan

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
