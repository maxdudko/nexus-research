"""Report adapter. Evidence ids and source metadata stay on the server."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nexus.ai.errors import ResearchError
from nexus.ai.synthesis import grounded_report
from nexus.research.budgets import charge_synthesis, synthesis_calls_allowed
from nexus.research.nodes.common import failed, stopped
from nexus.research.outcomes import SynthesisOutcome
from nexus.research.routing import route_decision
from nexus.research.state import ResearchState, UsageSnapshot
from nexus.schemas.report import ReportStatus, ResearchReport

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext


class StaticSynthesizer:
    """Deterministic report for the offline graph. It spends no model calls."""

    async def synthesize(
        self,
        state: ResearchState,
        *,
        status: ReportStatus,
        max_calls: int,
    ) -> SynthesisOutcome:
        del max_calls
        return SynthesisOutcome(
            report=grounded_report(state, status),
            llm_calls=0,
        )


async def synthesize_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Write the report the route already chose. Do not invent provenance."""
    if stopped(state):
        return {"report": None}
    decision = route_decision(state, runtime.context.limits, runtime.context.clock())
    if decision.route == "fail" or decision.report_status is None:
        code = state["error_code"] or "invalid_state"
        return failed(code)
    if not state["evidence"] or decision.report_status == "insufficient_evidence":
        report = grounded_report(state, "insufficient_evidence")
        return _published(state, report, decision.warning, None, None)
    limits = runtime.context.limits
    try:
        outcome = await runtime.context.synthesizer.synthesize(
            state,
            status=decision.report_status,
            max_calls=synthesis_calls_allowed(state["usage"], limits),
        )
    except asyncio.CancelledError:
        raise
    except ResearchError as exc:
        usage = _charge(state, runtime, 1)
        if exc.code == "provider_not_configured":
            return failed(exc.code, usage)
        report = grounded_report(state, "partial")
        return _published(state, report, decision.warning, exc.code, usage)
    except Exception:
        report = grounded_report(state, "partial")
        usage = _charge(state, runtime, 1)
        return _published(
            state, report, decision.warning, "provider_unavailable", usage
        )
    usage = _charge(state, runtime, outcome.llm_calls)
    if outcome.report is None:
        report = grounded_report(
            state,
            "partial" if state["evidence"] else "insufficient_evidence",
        )
        return _published(state, report, decision.warning, outcome.warning, usage)
    if outcome.report.status != decision.report_status:
        charged = usage if outcome.llm_calls > 0 else None
        return failed("invalid_model_output", charged)
    return _published(state, outcome.report, decision.warning, outcome.warning, usage)


def _published(
    state: ResearchState,
    report: object,
    route_warning: str | None,
    model_warning: str | None,
    usage: UsageSnapshot | None,
) -> dict[str, object]:
    if not isinstance(report, ResearchReport):
        return failed("invalid_model_output", usage)
    update: dict[str, object] = {
        "status": report.status,
        "report": report.model_dump(mode="json"),
        "error_code": None,
        "warnings": _warnings(state, route_warning, model_warning),
    }
    if usage is not None and usage is not state["usage"]:
        update["usage"] = usage
    return update


def _warnings(
    state: ResearchState,
    route_warning: str | None,
    model_warning: str | None,
) -> list[str]:
    warnings = list(state["warnings"])
    for code in (route_warning, model_warning):
        if code is not None and code.isidentifier() and code not in warnings:
            warnings.append(code)
    return warnings


def _charge(
    state: ResearchState,
    runtime: Runtime[GraphContext],
    calls: int,
) -> UsageSnapshot:
    if calls <= 0:
        return state["usage"]
    try:
        return charge_synthesis(state["usage"], runtime.context.limits, calls=calls)
    except ValueError:
        return state["usage"]
