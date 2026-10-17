"""Coverage adapter. The default stub never calls a model."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from nexus.ai.errors import ResearchError
from nexus.research.nodes.common import failed, stopped
from nexus.research.state import CoverageState, ResearchState

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


class StaticEvaluator:
    """Enough of an evaluator for the offline graph. Group B replaces it."""

    async def evaluate(self, state: ResearchState) -> CoverageState:
        del state
        return {
            "sufficient": False,
            "covered_task_ids": [],
            "missing_task_ids": [],
            "gaps": [],
            "uncertainty": "",
        }


async def evaluate_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Record coverage advice. Routing still applies the budget policy."""
    if stopped(state):
        return {}
    try:
        coverage = await runtime.context.evaluator.evaluate(state)
    except asyncio.CancelledError:
        raise
    except ResearchError as exc:
        return failed(exc.code)
    except Exception:
        return failed("provider_unavailable")
    checked = _coverage(coverage)
    if checked is None:
        return failed("invalid_model_output")
    return {"coverage": checked}


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
