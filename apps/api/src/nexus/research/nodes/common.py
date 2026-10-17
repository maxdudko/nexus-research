"""Shared node helpers. Nodes replace fields; they do not mutate the input."""

from nexus.research.state import ResearchState, UsageSnapshot

_STOPPED = {"failed", "interrupted"}


def stopped(state: ResearchState) -> bool:
    """A failed or interrupted run must not call another provider."""
    return state["status"] in _STOPPED


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
