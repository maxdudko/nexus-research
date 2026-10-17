"""Results from the evaluator and synthesizer, including the calls they spent."""

from dataclasses import dataclass

from nexus.research.state import CoverageState
from nexus.schemas.report import ResearchReport


@dataclass(frozen=True)
class EvaluationOutcome:
    """Coverage advice and the logical model calls behind it."""

    coverage: CoverageState
    llm_calls: int


@dataclass(frozen=True)
class SynthesisOutcome:
    """A grounded report, or none when the model output cannot be published."""

    report: ResearchReport | None
    llm_calls: int
    warning: str | None = None
