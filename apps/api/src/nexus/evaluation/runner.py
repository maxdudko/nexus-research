"""Run the offline suite, or the opted-in live judge."""

import json
from pathlib import Path

from pydantic import BaseModel

from nexus.core.config import Settings
from nexus.evaluation.calibration import calibrate, judge_counts
from nexus.evaluation.citations import check_citations
from nexus.evaluation.datasets import EvaluationDataset, load_dataset
from nexus.evaluation.grounding import evaluate_grounding, grounding_structure
from nexus.evaluation.retrieval import score_dataset
from nexus.evaluation.schemas import (
    JUDGE_TEMPERATURE,
    PROMPT_VERSION,
    K,
    LiveReport,
    OfflineReport,
)
from nexus.observability.pricing import PRICING_VERSION


def run_offline(dataset: EvaluationDataset) -> OfflineReport:
    """Score fixtures. Retrieval numbers are reported and do not fail the run."""
    return OfflineReport(
        dataset_version=dataset.version,
        k=K,
        retrieval=score_dataset(dataset),
        citations=check_citations(dataset),
        grounding=grounding_structure(dataset),
    )


def offline_failed(report: OfflineReport) -> bool:
    """True when provenance or grounding structure disagrees with the fixture."""
    citation_mismatch = any(
        case.citation_valid != case.expect_citation_valid
        for case in report.citations.cases
    )
    grounding_mismatch = any(
        case.structural_valid != case.expect_structurally_valid
        for case in report.grounding
    )
    return citation_mismatch or grounding_mismatch


async def run_live(dataset: EvaluationDataset, settings: Settings) -> LiveReport:
    """Call the live judge. The caller has already checked the opt-in flag."""
    from nexus.evaluation.judge import OpenAIGroundingJudge

    offline = run_offline(dataset)
    judge = OpenAIGroundingJudge(settings)
    grounding = await evaluate_grounding(dataset, judge)
    return LiveReport(
        dataset_version=dataset.version,
        k=K,
        model=settings.openai_model,
        prompt_version=PROMPT_VERSION,
        temperature=JUDGE_TEMPERATURE,
        pricing_version=PRICING_VERSION,
        retrieval=offline.retrieval,
        citations=offline.citations,
        grounding=grounding,
        counts=judge_counts(grounding),
        calibration=calibrate(grounding),
    )


def dump_report(report: BaseModel) -> str:
    """Serialize with sorted keys so two offline runs match byte for byte."""
    payload: object = report.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_report(path: Path, report: BaseModel) -> None:
    """Create parent directories and write one JSON artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_report(report), encoding="utf-8")


def load_or_raise(directory: Path | None) -> EvaluationDataset:
    """Load fixtures. ``DatasetError`` is the schema failure the CLI exits on."""
    return load_dataset(directory)
