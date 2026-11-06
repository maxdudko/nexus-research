"""Budgeted evidence analysis, failing to incomplete coverage on errors."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nexus.research.budgets import work_seconds
from nexus.research.nodes.common import before_provider, warnings_with
from nexus.research.state import ResearchState, copy_usage

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext


async def analyze_node(
    state: ResearchState, runtime: Runtime[GraphContext]
) -> dict[str, object]:
    gate = await before_provider(state, runtime.context.session_active)
    if gate is not None:
        return gate
    if runtime.context.analyzer is None or not state["evidence"]:
        return {"analysis": None}
    usage = copy_usage(state["usage"])
    # Fund this analysis call, section completion and three verification calls.
    limits = runtime.context.limits
    if usage["llm_calls"] + limits.max_synthesis_calls + 4 > limits.max_llm_calls:
        return {"analysis": None, "warnings": warnings_with(state, "budget_exhausted")}
    usage["llm_calls"] += 1
    try:
        async with asyncio.timeout(
            work_seconds(state, runtime.context.limits, runtime.context.clock())
        ):
            result = await runtime.context.analyzer.analyze(state)
    except asyncio.CancelledError:
        raise
    except Exception:
        return {
            "analysis": None,
            "usage": usage,
            "warnings": warnings_with(state, "analysis_failed"),
        }
    return {"analysis": result.model_dump(mode="json"), "usage": usage}
