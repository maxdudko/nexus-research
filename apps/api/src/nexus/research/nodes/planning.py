"""Planning adapter. The node calls Planner and stores the validated plan."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nexus.ai.errors import ResearchError
from nexus.research.budgets import charge_planner
from nexus.research.nodes.common import failed, stopped
from nexus.research.state import ResearchState, UsageSnapshot

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext


async def plan_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Create one plan. A provider failure ends the run with no report."""
    if stopped(state):
        return {}
    limits = runtime.context.limits
    try:
        plan = await runtime.context.planner.create_plan(state["question"])
    except asyncio.CancelledError:
        raise
    except ResearchError as exc:
        return failed(exc.code, _charge(state, runtime))
    except Exception:
        return failed("provider_unavailable", _charge(state, runtime))
    update: dict[str, object] = {
        "plan": plan.model_dump(mode="json"),
        "usage": charge_planner(state["usage"], limits),
    }
    return update


def _charge(state: ResearchState, runtime: Runtime[GraphContext]) -> UsageSnapshot:
    try:
        return charge_planner(state["usage"], runtime.context.limits)
    except ValueError:
        usage = state["usage"]
        copied: UsageSnapshot = {
            "search_calls": usage["search_calls"],
            "llm_calls": usage["llm_calls"],
            "pages_indexed": usage["pages_indexed"],
            "pages_failed": usage["pages_failed"],
        }
        return copied
