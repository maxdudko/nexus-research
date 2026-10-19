"""Prepare cited excerpts. This module does not decide entailment."""

from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import ValidationError

from nexus.evaluation.datasets import Catalog, EvaluationDataset
from nexus.evaluation.schemas import (
    EXCERPT_CHARS,
    GroundingCase,
    GroundingJudgeResult,
    GroundingStructureResult,
    JudgeVerdict,
    PreparedClaim,
    PreparedExcerpt,
    SupportLabel,
    UsageSourceName,
)

LABELS: tuple[SupportLabel, ...] = (
    "supported",
    "unsupported",
    "insufficient_context",
    "contradictory",
)


class JudgeFailure(Exception):
    """A judge failure whose message is a fixed code, not provider text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.judge_code = code


class JudgeCall:
    """What one judge implementation returns to the orchestrator."""

    def __init__(
        self,
        *,
        verdict: JudgeVerdict | None,
        outcome: Literal["ok", "error"],
        model: str,
        prompt_version: str,
        temperature: float,
        timestamp: str,
        input_tokens: int | None,
        output_tokens: int | None,
        usage_source: UsageSourceName,
        error_code: str | None = None,
    ) -> None:
        self.verdict = verdict
        self.outcome: Literal["ok", "error"] = outcome
        self.model = model
        self.prompt_version = prompt_version
        self.temperature = temperature
        self.timestamp = timestamp
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.usage_source: UsageSourceName = usage_source
        self.error_code = error_code


class GroundingJudge(Protocol):
    """A judge. Constructing an implementation must not call a provider."""

    async def judge(self, prepared: PreparedClaim) -> JudgeCall: ...


def prepare_claim(case: GroundingCase, catalog: Catalog) -> PreparedClaim | str:
    """Keep cited excerpts from one research scope, each cut at 500 characters.

    An unknown citation or a passage from another research scope is rejected.
    The returned text is not a judgment about the claim.
    """
    excerpts: list[PreparedExcerpt] = []
    for semantic_id in case.cited_semantic_ids:
        chunk = catalog.chunks.get(semantic_id)
        if chunk is None:
            return "unknown_citation"
        if chunk.research_id != case.research_id:
            return "foreign_research"
        excerpts.append(
            PreparedExcerpt(
                semantic_id=semantic_id,
                text=chunk.text[:EXCERPT_CHARS],
            )
        )
    return PreparedClaim(case_id=case.case_id, claim=case.claim, excerpts=excerpts)


def grounding_structure(dataset: EvaluationDataset) -> list[GroundingStructureResult]:
    """Record whether each claim is safe to send to a judge."""
    results: list[GroundingStructureResult] = []
    ordered = sorted(dataset.grounding, key=lambda item: item.case_id)
    for case in ordered:
        prepared = prepare_claim(case, dataset.catalog)
        if isinstance(prepared, str):
            results.append(
                GroundingStructureResult(
                    case_id=case.case_id,
                    gold_label=case.gold_label,
                    structural_valid=False,
                    expect_structurally_valid=case.expect_structurally_valid,
                    error_code=prepared,
                    excerpt_count=0,
                )
            )
            continue
        results.append(
            GroundingStructureResult(
                case_id=case.case_id,
                gold_label=case.gold_label,
                structural_valid=True,
                expect_structurally_valid=case.expect_structurally_valid,
                excerpt_count=len(prepared.excerpts),
            )
        )
    return results


async def evaluate_grounding(
    dataset: EvaluationDataset,
    judge: GroundingJudge,
) -> list[GroundingJudgeResult]:
    """Run a judge on structurally valid claims. Invalid claims are not sent."""
    results: list[GroundingJudgeResult] = []
    ordered = sorted(dataset.grounding, key=lambda item: item.case_id)
    for case in ordered:
        prepared = prepare_claim(case, dataset.catalog)
        if isinstance(prepared, str):
            results.append(_skipped(case, dataset.version, prepared))
            continue
        try:
            call = await _invoke(judge, prepared)
        except Exception as exc:
            results.append(_failed(case, dataset.version, classify_judge_error(exc)))
            continue
        results.append(_from_call(case, dataset.version, prepared, call))
    return results


def classify_judge_error(exc: Exception) -> str:
    """Map a failure to a fixed code. The exception text is discarded."""
    if isinstance(exc, JudgeFailure):
        return exc.judge_code
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "timeout"
    if isinstance(exc, ValidationError):
        return "invalid_model_output"
    return "provider_unavailable"


def utc_timestamp() -> str:
    """A second-resolution UTC timestamp for one judge observation."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _invoke(judge: GroundingJudge, prepared: PreparedClaim) -> JudgeCall:
    call = await judge.judge(prepared)
    if not isinstance(call, JudgeCall):
        raise JudgeFailure("invalid_model_output")
    return call


def _skipped(
    case: GroundingCase,
    dataset_version: str,
    error_code: str,
) -> GroundingJudgeResult:
    return GroundingJudgeResult(
        case_id=case.case_id,
        gold_label=case.gold_label,
        structural_valid=False,
        outcome="skipped",
        dataset_version=dataset_version,
        error_code=error_code,
    )


def _failed(
    case: GroundingCase,
    dataset_version: str,
    error_code: str,
) -> GroundingJudgeResult:
    return GroundingJudgeResult(
        case_id=case.case_id,
        gold_label=case.gold_label,
        structural_valid=True,
        outcome="error",
        dataset_version=dataset_version,
        error_code=error_code,
    )


def _from_call(
    case: GroundingCase,
    dataset_version: str,
    prepared: PreparedClaim,
    call: JudgeCall,
) -> GroundingJudgeResult:
    verdict = call.verdict
    error_code = call.error_code
    outcome = call.outcome
    predicted: SupportLabel | None = None
    rationale: str | None = None
    if outcome == "ok" and verdict is not None:
        allowed = {excerpt.semantic_id for excerpt in prepared.excerpts}
        if any(evidence_id not in allowed for evidence_id in verdict.evidence_ids):
            outcome = "error"
            error_code = "unknown_citation"
        else:
            predicted = verdict.verdict
            rationale = verdict.rationale
    elif outcome == "ok":
        outcome = "error"
        error_code = "invalid_model_output"
    return GroundingJudgeResult(
        case_id=case.case_id,
        gold_label=case.gold_label,
        structural_valid=True,
        predicted_label=predicted,
        outcome="ok" if outcome == "ok" else "error",
        model=call.model,
        prompt_version=call.prompt_version,
        dataset_version=dataset_version,
        temperature=call.temperature,
        timestamp=call.timestamp,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        usage_source=_usage(call.usage_source),
        error_code=error_code,
        rationale=rationale,
    )


def _usage(value: str) -> UsageSourceName:
    if value == "provider":
        return "provider"
    if value == "estimated":
        return "estimated"
    return "unavailable"


def valid_case_count(dataset: EvaluationDataset) -> int:
    """How many claims the live judge would actually call."""
    total = 0
    for case in dataset.grounding:
        if not isinstance(prepare_claim(case, dataset.catalog), str):
            total += 1
    return total
