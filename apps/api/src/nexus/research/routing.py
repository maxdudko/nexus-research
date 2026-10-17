"""Deterministic routes. The evaluator recommends; this module decides."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nexus.research.budgets import (
    BudgetLimits,
    can_discover,
    is_new_batch,
    next_query_batch,
)
from nexus.research.state import (
    EvidenceState,
    GraphStateError,
    ResearchState,
    validate_state,
)
from nexus.schemas.report import EvidenceRef, ReportStatus

RouteName = Literal[
    "continue_discovery",
    "synthesize",
    "insufficient_evidence",
    "fail",
]
PlanRoute = Literal["discover", "fail"]

DEADLINE_EXCEEDED = "deadline_exceeded"
NO_PROGRESS = "no_progress"
BUDGET_EXHAUSTED = "budget_exhausted"
DUPLICATE_QUERY_BATCH = "duplicate_query_batch"
INVALID_STATE = "invalid_state"


@dataclass(frozen=True)
class RouteDecision:
    """Where the graph goes next, and the report status when it stops."""

    route: RouteName
    warning: str | None
    report_status: ReportStatus | None


def decide(state: ResearchState, limits: BudgetLimits, now: datetime) -> RouteName:
    """The conditional edge after evaluation."""
    return route_decision(state, limits, now).route


def route_decision(
    state: ResearchState,
    limits: BudgetLimits,
    now: datetime,
) -> RouteDecision:
    """Stop for a deadline, a sufficient report, or no safe new work."""
    try:
        validate_state(state)
        return _route(state, limits, now)
    except GraphStateError, ValueError:
        return RouteDecision("fail", INVALID_STATE, None)


def route_after_plan(state: ResearchState) -> PlanRoute:
    """Discovery runs only after a usable plan. A planner failure ends the run."""
    try:
        validate_state(state)
    except GraphStateError:
        return "fail"
    if state["status"] != "running" or state["plan"] is None:
        return "fail"
    return "discover"


def assign_evidence_ids(passages: Sequence[EvidenceState]) -> list[EvidenceRef]:
    """Assign E1..En from score, then source id, then chunk id."""
    ordered = sorted(
        passages,
        key=lambda item: (-item["score"], item["source_id"], item["chunk_id"]),
    )
    return [
        EvidenceRef(
            evidence_id=f"E{index}",
            source_id=item["source_id"],
            chunk_id=item["chunk_id"],
            research_id=item["research_id"],
            url=item["url"],
            page_number=item["page_number"],
        )
        for index, item in enumerate(ordered, start=1)
    ]


def _route(
    state: ResearchState,
    limits: BudgetLimits,
    now: datetime,
) -> RouteDecision:
    if state["status"] in {"failed", "interrupted"}:
        return RouteDecision("fail", None, None)
    if state["status"] != "running" or state["plan"] is None:
        return RouteDecision("fail", INVALID_STATE, None)
    if _deadline_exceeded(state, limits, now):
        return _finish(state, DEADLINE_EXCEEDED)
    if _coverage_sufficient(state) and state["evidence"]:
        return RouteDecision("synthesize", None, "completed")
    if _can_refine(state, limits):
        return RouteDecision("continue_discovery", None, None)
    return _finish(state, _stop_warning(state, limits))


def _finish(state: ResearchState, warning: str) -> RouteDecision:
    if state["evidence"]:
        return RouteDecision("synthesize", warning, "partial")
    return RouteDecision("insufficient_evidence", warning, "insufficient_evidence")


def _can_refine(state: ResearchState, limits: BudgetLimits) -> bool:
    if not can_discover(state["usage"], limits, state["iteration"]):
        return False
    if not _made_progress(state):
        return False
    batch = next_query_batch(
        state["plan"],
        state["query_batches"],
        state["usage"],
        limits,
        state["iteration"],
    )
    return is_new_batch(batch, state["query_batches"])


def _stop_warning(state: ResearchState, limits: BudgetLimits) -> str:
    if not can_discover(state["usage"], limits, state["iteration"]):
        return BUDGET_EXHAUSTED
    if not _made_progress(state):
        return NO_PROGRESS
    batch = next_query_batch(
        state["plan"],
        state["query_batches"],
        state["usage"],
        limits,
        state["iteration"],
    )
    if not is_new_batch(batch, state["query_batches"]):
        return DUPLICATE_QUERY_BATCH
    return BUDGET_EXHAUSTED


def _made_progress(state: ResearchState) -> bool:
    progress = state["iteration_progress"]
    if state["usage"]["pages_indexed"] > progress["pages_indexed"]:
        return True
    seen = set(progress["chunk_ids"])
    return any(item["chunk_id"] not in seen for item in state["evidence"])


def _coverage_sufficient(state: ResearchState) -> bool:
    coverage = state["coverage"]
    if coverage is None:
        return False
    return coverage["sufficient"] is True


def _deadline_exceeded(
    state: ResearchState,
    limits: BudgetLimits,
    now: datetime,
) -> bool:
    started = datetime.fromisoformat(state["started_at"])
    if started.tzinfo is None or now.tzinfo is None:
        raise GraphStateError("deadlines require timezone-aware times")
    elapsed = (now - started).total_seconds()
    return elapsed >= limits.deadline_seconds
