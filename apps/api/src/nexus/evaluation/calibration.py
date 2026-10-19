"""Confusion matrix for gold labels versus judge labels. Both are estimates."""

from collections.abc import Sequence
from decimal import Decimal

from nexus.evaluation.retrieval import QUANT
from nexus.evaluation.schemas import (
    CalibrationReport,
    GroundingJudgeResult,
    JudgeRunCounts,
    SupportLabel,
)

LABELS: tuple[SupportLabel, ...] = (
    "supported",
    "unsupported",
    "insufficient_context",
    "contradictory",
)


def judge_counts(results: Sequence[GroundingJudgeResult]) -> JudgeRunCounts:
    """Count calls. A label disagreement is not a provider failure."""
    attempted = 0
    succeeded = 0
    failed = 0
    skipped = 0
    disagreed = 0
    for result in results:
        if not result.structural_valid or result.outcome == "skipped":
            skipped += 1
            continue
        attempted += 1
        if result.outcome == "ok":
            succeeded += 1
            if result.predicted_label != result.gold_label:
                disagreed += 1
        else:
            failed += 1
    return JudgeRunCounts(
        attempted=attempted,
        succeeded=succeeded,
        failed=failed,
        skipped=skipped,
        disagreed=disagreed,
    )


def calibrate(results: Sequence[GroundingJudgeResult]) -> CalibrationReport:
    """Count agreements. Skipped and failed calls are left out of the matrix."""
    matrix: dict[str, dict[str, int]] = {
        gold: {predicted: 0 for predicted in LABELS} for gold in LABELS
    }
    compared = 0
    agreements = 0
    for result in results:
        predicted = result.predicted_label
        if result.outcome != "ok" or predicted is None:
            continue
        if result.gold_label not in matrix or predicted not in matrix:
            continue
        matrix[result.gold_label][predicted] += 1
        compared += 1
        if result.gold_label == predicted:
            agreements += 1
    agreement = None
    if compared:
        ratio = Decimal(agreements) / Decimal(compared)
        agreement = format(ratio.quantize(QUANT), "f")
    return CalibrationReport(
        labels=list(LABELS),
        matrix=matrix,
        compared=compared,
        agreements=agreements,
        agreement=agreement,
    )
