"""Terminal failure. This node does not call a provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.research.state import ResearchState

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext


async def fail_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Keep an existing failure, or record that the state could not continue."""
    _ = runtime
    if state["status"] == "interrupted":
        return {
            "report": None,
            "error_code": state["error_code"] or "interrupted",
        }
    if state["status"] == "failed" and state["error_code"]:
        return {"report": None}
    return {
        "status": "failed",
        "report": None,
        "error_code": state["error_code"] or "invalid_state",
    }
