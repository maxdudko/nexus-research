"""Coverage advice from the evaluator. Bounds are checked in application code."""

from collections.abc import Sequence

from pydantic import BaseModel, ValidationError

MAX_GAPS = 3
MAX_COVERAGE_TEXT = 500


class CoverageEvaluation(BaseModel):
    """Provider-facing coverage. Task ids are assigned by the server."""

    sufficient: bool
    covered_task_ids: list[str]
    missing_task_ids: list[str]
    gaps: list[str]
    uncertainty: str


def task_label(index: int) -> str:
    """One-based position in the stored plan. The planner schema has no id."""
    if index < 1:
        raise ValueError("task index starts at 1")
    return f"T{index}"


def task_labels(count: int) -> list[str]:
    """Labels for a plan that already passed the one-to-three task bound."""
    if not 1 <= count <= MAX_GAPS:
        raise ValueError("tasks must contain 1 to 3 items")
    return [task_label(index) for index in range(1, count + 1)]


def validate_coverage(
    result: object,
    *,
    task_ids: Sequence[str],
    has_evidence: bool,
) -> CoverageEvaluation:
    """Reject unknown ids, overlaps, long gaps, and an unearned sufficient flag."""
    parsed = _coerce(result)
    covered = list(parsed.covered_task_ids)
    missing = list(parsed.missing_task_ids)
    known = set(task_ids)
    if _invalid_partition(covered, missing, known):
        raise ValueError("coverage is not valid")
    if len(parsed.gaps) > MAX_GAPS:
        raise ValueError("coverage is not valid")
    for gap in parsed.gaps:
        if len(gap) > MAX_COVERAGE_TEXT:
            raise ValueError("coverage is not valid")
    if len(parsed.uncertainty) > MAX_COVERAGE_TEXT:
        raise ValueError("coverage is not valid")
    if parsed.sufficient and (missing or not has_evidence):
        raise ValueError("coverage is not valid")
    return parsed


def _coerce(result: object) -> CoverageEvaluation:
    if isinstance(result, CoverageEvaluation):
        return result
    if isinstance(result, dict):
        try:
            return CoverageEvaluation.model_validate(result)
        except ValidationError:
            raise ValueError("coverage is not valid") from None
    raise ValueError("coverage is not valid")


def _invalid_partition(
    covered: list[str],
    missing: list[str],
    known: set[str],
) -> bool:
    if len(covered) != len(set(covered)) or len(missing) != len(set(missing)):
        return True
    if set(covered) & set(missing):
        return True
    return set(covered) | set(missing) != known
