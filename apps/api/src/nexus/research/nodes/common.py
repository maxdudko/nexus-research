"""Shared node helpers. Nodes replace fields; they do not mutate the input."""

from collections.abc import Awaitable, Callable

from nexus.research.state import ResearchState, UsageSnapshot

_STOPPED = {"failed", "interrupted"}


def stopped(state: ResearchState) -> bool:
    """A failed or interrupted run must not call another provider."""
    return state["status"] in _STOPPED


async def before_provider(
    state: ResearchState,
    session_active: Callable[[], Awaitable[bool]],
) -> dict[str, object] | None:
    """Return an update when this node must not call a provider."""
    if stopped(state):
        return {}
    if await session_active():
        return None
    return {
        "status": "interrupted",
        "report": None,
        "error_code": "interrupted",
    }


def warnings_with(state: ResearchState, code: str) -> list[str]:
    """Copy warnings and append one code. Free text is not a warning."""
    warnings = list(state["warnings"])
    if code.isidentifier() and code not in warnings:
        warnings.append(code)
    return warnings


def failed(code: str, usage: UsageSnapshot | None = None) -> dict[str, object]:
    """Terminal failure. The report stays empty."""
    update: dict[str, object] = {
        "status": "failed",
        "error_code": code,
        "report": None,
    }
    if usage is not None:
        update["usage"] = usage
    return update
