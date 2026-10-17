"""Discovery adapter. The question carries the selected queries; snippets do not."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nexus.ai.errors import ResearchError
from nexus.research.budgets import (
    add_candidates,
    can_discover,
    charge_discovery,
    discovery_question,
    next_query_batch,
)
from nexus.research.nodes.common import failed, stopped, warnings_with
from nexus.research.state import ResearchState, UsageSnapshot, snapshot_progress

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext


async def discover_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Run one Discovery call when the worst-case nested budget still fits."""
    if stopped(state):
        return {}
    limits = runtime.context.limits
    if state["plan"] is None or not can_discover(
        state["usage"], limits, state["iteration"]
    ):
        return {"warnings": warnings_with(state, "budget_exhausted")}
    batch = next_query_batch(
        state["plan"],
        state["query_batches"],
        state["usage"],
        limits,
        state["iteration"],
    )
    if not batch:
        return {"warnings": warnings_with(state, "budget_exhausted")}
    started: dict[str, object] = {
        "iteration": state["iteration"] + 1,
        "iteration_progress": snapshot_progress(state),
        "query_batches": [*state["query_batches"], batch],
    }
    try:
        result = await runtime.context.discovery.discover(
            discovery_question(state["question"], batch)
        )
    except asyncio.CancelledError:
        raise
    except ResearchError:
        # The provider may have spent its internal cap before raising.
        return {
            **started,
            "warnings": warnings_with(state, "discovery_failed"),
            "usage": _reserve(state, runtime),
        }
    except Exception:
        return {
            **started,
            "warnings": warnings_with(state, "discovery_failed"),
            "usage": _reserve(state, runtime),
        }
    try:
        usage = charge_discovery(
            state["usage"],
            limits,
            search_calls=result.search_calls,
            llm_calls=result.llm_calls,
        )
    except ValueError:
        update = failed("provider_unavailable", _reserve(state, runtime))
        update.update(started)
        return update
    warnings = list(state["warnings"])
    for code in result.warnings:
        if code.isidentifier() and code not in warnings:
            warnings.append(code)
    return {
        **started,
        "usage": usage,
        "warnings": warnings,
        "candidate_urls": add_candidates(
            state["candidate_urls"],
            [(source.url, source.title) for source in result.sources],
        ),
    }


def _reserve(state: ResearchState, runtime: Runtime[GraphContext]) -> UsageSnapshot:
    """Charge Discovery's ceiling when the actual counts never came back."""
    limits = runtime.context.limits
    try:
        return charge_discovery(
            state["usage"],
            limits,
            search_calls=limits.discovery_search_ceiling,
            llm_calls=limits.discovery_llm_ceiling,
        )
    except ValueError:
        usage = state["usage"]
        copied: UsageSnapshot = {
            "search_calls": usage["search_calls"],
            "llm_calls": usage["llm_calls"],
            "pages_indexed": usage["pages_indexed"],
            "pages_failed": usage["pages_failed"],
        }
        return copied
