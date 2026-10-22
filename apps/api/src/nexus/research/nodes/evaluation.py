"""Coverage adapter. A model failure does not become a successful claim."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from nexus.ai.errors import ResearchError
from nexus.research.budgets import charge_evaluation, evaluation_call_allowed
from nexus.research.nodes.common import before_provider, failed, warnings_with
from nexus.research.outcomes import EvaluationOutcome
from nexus.research.state import CoverageState, ResearchState, UsageSnapshot
from nexus.schemas.coverage import task_labels
from nexus.schemas.research import ResearchPlan

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext

_COVERAGE_KEYS = {
    "sufficient",
    "covered_task_ids",
    "missing_task_ids",
    "gaps",
    "uncertainty",
}
_NO_EVIDENCE = "No evidence was retrieved."
_UNTRUSTED = "Coverage could not be judged."


class StaticEvaluator:
    """Offline evaluator. It does not call a model or spend the reserve."""

    async def evaluate(self, state: ResearchState) -> EvaluationOutcome:
        del state
        return EvaluationOutcome(
            coverage={
                "sufficient": False,
                "covered_task_ids": [],
                "missing_task_ids": [],
                "gaps": [],
                "uncertainty": "",
            },
            llm_calls=0,
        )


async def evaluate_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Record coverage advice. Routing still applies the budget policy."""
    gate = await before_provider(state, runtime.context.session_active)
    if gate is not None:
        return gate
    if not state["evidence"]:
        return {"coverage": _untrusted_coverage(state, _NO_EVIDENCE)}
    limits = runtime.context.limits
    if not evaluation_call_allowed(state["usage"], limits):
        return _soft(state, "budget_exhausted", state["usage"])
    try:
        outcome = await runtime.context.evaluator.evaluate(state)
    except asyncio.CancelledError:
        raise
    except ResearchError as exc:
        usage = _charge(state, runtime, 1)
        if exc.code == "provider_not_configured":
            return failed(exc.code, usage)
        return _soft(state, exc.code, usage)
    except Exception:
        return _soft(state, "provider_unavailable", _charge(state, runtime, 1))
    calls = outcome.llm_calls if 0 <= outcome.llm_calls <= 1 else 1
    usage = _charge(state, runtime, calls)
    checked = _coverage(outcome.coverage)
    if checked is None:
        return _soft(state, "invalid_model_output", usage)
    update: dict[str, object] = {"coverage": checked}
    if calls > 0:
        update["usage"] = usage
    return update


def _soft(
    state: ResearchState,
    warning: str,
    usage: UsageSnapshot,
) -> dict[str, object]:
    update: dict[str, object] = {
        "coverage": _untrusted_coverage(state, _UNTRUSTED),
        "warnings": warnings_with(state, warning),
    }
    if usage is not state["usage"]:
        update["usage"] = usage
    return update


def _charge(
    state: ResearchState,
    runtime: Runtime[GraphContext],
    calls: int,
) -> UsageSnapshot:
    if calls <= 0:
        return state["usage"]
    try:
        return charge_evaluation(state["usage"], runtime.context.limits, calls=calls)
    except ValueError:
        return state["usage"]


def _untrusted_coverage(state: ResearchState, note: str) -> CoverageState:
    return {
        "sufficient": False,
        "covered_task_ids": [],
        "missing_task_ids": _task_ids(state),
        "gaps": [],
        "uncertainty": note[:500],
    }


def _task_ids(state: ResearchState) -> list[str]:
    plan = state["plan"]
    if plan is None:
        return []
    try:
        parsed = ResearchPlan.model_validate(plan)
    except ValidationError:
        return []
    return task_labels(len(parsed.tasks))


def _coverage(value: object) -> CoverageState | None:
    if not isinstance(value, dict) or set(value) != _COVERAGE_KEYS:
        return None
    if not isinstance(value["sufficient"], bool):
        return None
    for key in ("covered_task_ids", "missing_task_ids", "gaps"):
        items = value[key]
        if not isinstance(items, list) or any(
            not isinstance(item, str) for item in items
        ):
            return None
    if not isinstance(value["uncertainty"], str):
        return None
    return cast(CoverageState, value)
