"""Compare two stored evaluations. This module does not score or query."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from nexus.admin.schemas import (
    CitationDeltas,
    CitationSummary,
    ComparisonDeltas,
    EvaluationDetail,
    GroundingDeltas,
    GroundingSummary,
    JudgeCountDeltas,
    LiveConfig,
    PricingReason,
    ReasonCode,
    RetrievalDeltas,
    RetrievalSummary,
    WarningCode,
)
from nexus.evaluation.schemas import CalibrationReport, JudgeRunCounts

_QUANT = Decimal("0.000001")


@dataclass(frozen=True)
class RunView:
    """The stored fields that decide compatibility and deltas."""

    suite: str
    dataset_sha256: str
    dataset_version: str
    report_version: str
    scorer_version: str
    k: int
    model: str | None
    prompt_version: str | None
    temperature: Decimal | None
    label_taxonomy: tuple[str, ...] | None
    code_revision: str | None
    executed_at: datetime | None
    pricing_version: str | None
    retrieval: RetrievalSummary
    citations: CitationSummary
    grounding: GroundingSummary
    counts: JudgeRunCounts | None
    calibration: CalibrationReport | None


@dataclass(frozen=True)
class ComparisonResult:
    """Reasons, warnings, and deltas. Deltas are absent when the pair differs."""

    comparable: bool
    reasons: tuple[ReasonCode, ...]
    warnings: tuple[WarningCode, ...]
    cost_comparable: bool
    pricing_reason: PricingReason | None
    deltas: ComparisonDeltas | None


def view_from_detail(detail: EvaluationDetail) -> RunView:
    """Copy the fields comparison is allowed to read."""
    taxonomy = None
    if type(detail.config) is LiveConfig:
        taxonomy = tuple(detail.config.label_taxonomy)
    temperature = None
    if detail.temperature is not None:
        temperature = Decimal(detail.temperature)
    return RunView(
        suite=detail.suite,
        dataset_sha256=detail.dataset_sha256,
        dataset_version=detail.dataset_version,
        report_version=detail.report_version,
        scorer_version=detail.scorer_version,
        k=detail.k,
        model=detail.model,
        prompt_version=detail.prompt_version,
        temperature=temperature,
        label_taxonomy=taxonomy,
        code_revision=detail.code_revision,
        executed_at=detail.executed_at,
        pricing_version=detail.pricing_version,
        retrieval=detail.retrieval,
        citations=detail.citations,
        grounding=detail.grounding,
        counts=detail.counts,
        calibration=detail.calibration,
    )


def compare_runs(left: RunView, right: RunView) -> ComparisonResult:
    """Return signed deltas only when the metric contract matches."""
    reasons = _reasons(left, right)
    cost_comparable, pricing_reason = _pricing(
        left.pricing_version,
        right.pricing_version,
    )
    deltas = None
    if not reasons:
        deltas = _deltas(left, right)
    return ComparisonResult(
        comparable=not reasons,
        reasons=reasons,
        warnings=_warnings(left, right),
        cost_comparable=cost_comparable,
        pricing_reason=pricing_reason,
        deltas=deltas,
    )


def _reasons(left: RunView, right: RunView) -> tuple[ReasonCode, ...]:
    reasons: list[ReasonCode] = []
    if left.suite != right.suite:
        reasons.append("suite_mismatch")
    if (
        left.dataset_sha256 != right.dataset_sha256
        or left.dataset_version != right.dataset_version
    ):
        reasons.append("dataset_mismatch")
    if left.report_version != right.report_version:
        reasons.append("report_version_mismatch")
    if left.scorer_version != right.scorer_version:
        reasons.append("scorer_mismatch")
    if left.k != right.k:
        reasons.append("k_mismatch")
    if left.suite == "live" and right.suite == "live" and _judge_differs(left, right):
        reasons.append("judge_config_mismatch")
    return tuple(reasons)


def _judge_differs(left: RunView, right: RunView) -> bool:
    return (
        left.model != right.model
        or left.prompt_version != right.prompt_version
        or left.temperature != right.temperature
        or left.label_taxonomy != right.label_taxonomy
    )


def _warnings(left: RunView, right: RunView) -> tuple[WarningCode, ...]:
    warnings: list[WarningCode] = []
    if left.executed_at is None or right.executed_at is None:
        warnings.append("execution_time_unknown")
    if left.code_revision is None or right.code_revision is None:
        warnings.append("code_revision_unknown")
    return tuple(warnings)


def _pricing(
    left: str | None,
    right: str | None,
) -> tuple[bool, PricingReason | None]:
    if left is None or right is None:
        return False, "pricing_unavailable"
    if left != right:
        return False, "pricing_version_mismatch"
    return True, None


def _deltas(left: RunView, right: RunView) -> ComparisonDeltas:
    live = left.suite == "live" and right.suite == "live"
    agreement = None
    counts = None
    matrix = None
    if live:
        agreement = _metric_delta(
            _agreement(left.calibration),
            _agreement(right.calibration),
        )
        counts = _count_delta(left.counts, right.counts)
        matrix = _matrix_delta(left.calibration, right.calibration)
    return ComparisonDeltas(
        retrieval=RetrievalDeltas(
            recall_at_k=_metric_delta(
                left.retrieval.recall_at_k,
                right.retrieval.recall_at_k,
            ),
            precision_at_k=_metric_delta(
                left.retrieval.precision_at_k,
                right.retrieval.precision_at_k,
            ),
            mrr_at_k=_metric_delta(left.retrieval.mrr_at_k, right.retrieval.mrr_at_k),
            defined_cases=right.retrieval.defined_cases - left.retrieval.defined_cases,
            undefined_cases=(
                right.retrieval.undefined_cases - left.retrieval.undefined_cases
            ),
        ),
        citations=CitationDeltas(
            cases=right.citations.cases - left.citations.cases,
            valid=right.citations.valid - left.citations.valid,
            invalid=right.citations.invalid - left.citations.invalid,
        ),
        grounding=GroundingDeltas(
            cases=right.grounding.cases - left.grounding.cases,
            structurally_valid=(
                right.grounding.structurally_valid - left.grounding.structurally_valid
            ),
            structurally_invalid=(
                right.grounding.structurally_invalid
                - left.grounding.structurally_invalid
            ),
        ),
        agreement=agreement,
        counts=counts,
        matrix=matrix,
    )


def _agreement(report: CalibrationReport | None) -> str | None:
    if report is None:
        return None
    return report.agreement


def _metric_delta(left: str | None, right: str | None) -> str | None:
    if left is None or right is None:
        return None
    return format((Decimal(right) - Decimal(left)).quantize(_QUANT), "f")


def _count_delta(
    left: JudgeRunCounts | None,
    right: JudgeRunCounts | None,
) -> JudgeCountDeltas | None:
    if left is None or right is None:
        return None
    return JudgeCountDeltas(
        attempted=right.attempted - left.attempted,
        succeeded=right.succeeded - left.succeeded,
        failed=right.failed - left.failed,
        skipped=right.skipped - left.skipped,
        disagreed=right.disagreed - left.disagreed,
    )


def _matrix_delta(
    left: CalibrationReport | None,
    right: CalibrationReport | None,
) -> dict[str, dict[str, int]] | None:
    if left is None or right is None:
        return None
    delta: dict[str, dict[str, int]] = {}
    for gold in left.labels:
        left_row = left.matrix.get(gold, {})
        right_row = right.matrix.get(gold, {})
        delta[gold] = {
            predicted: (
                int(right_row.get(predicted, 0)) - int(left_row.get(predicted, 0))
            )
            for predicted in left.labels
        }
    return delta
