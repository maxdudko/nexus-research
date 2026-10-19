"""Opt-in grounding judge. Default pytest does not collect this module."""

import os
from dataclasses import replace

import pytest
from tests.conftest import make_settings

from nexus.evaluation.datasets import load_dataset
from nexus.evaluation.grounding import LABELS, evaluate_grounding
from nexus.evaluation.judge import OpenAIGroundingJudge
from nexus.schemas.report import ResearchReport

pytestmark = pytest.mark.live


async def test_live_judge_returns_a_verdict_without_editing_a_report() -> None:
    if os.environ.get("NEXUS_RUN_LIVE_AI") != "1":
        pytest.skip("set NEXUS_RUN_LIVE_AI=1 to call the grounding judge")
    key = os.environ.get("NEXUS_OPENAI_API_KEY")
    if not key:
        pytest.skip("NEXUS_OPENAI_API_KEY is required")
    settings = make_settings(
        openai_api_key=key,
        openai_model=os.environ.get("NEXUS_OPENAI_MODEL", "gpt-4o-mini"),
    )
    dataset = load_dataset()
    case = next(item for item in dataset.grounding if item.case_id == "g01-supported")
    report = ResearchReport(
        title="Limits",
        summary="A short summary that the judge must leave unchanged.",
        sections=[],
        limitations=[],
        status="insufficient_evidence",
        sources=[],
    )
    before = report.model_dump()

    results = await evaluate_grounding(
        replace(dataset, grounding=(case,)),
        OpenAIGroundingJudge(settings),
    )

    assert report.model_dump() == before
    observed = results[0]
    assert observed.outcome == "ok"
    assert observed.predicted_label in LABELS
    assert observed.model == settings.openai_model
    assert observed.prompt_version
    assert observed.dataset_version == dataset.version
    assert observed.temperature == 0.0
    assert observed.timestamp
    assert observed.usage_source in {"provider", "unavailable"}
