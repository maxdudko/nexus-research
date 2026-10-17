"""Report adapter. Evidence ids are assigned here, not by a model."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nexus.ai.errors import ResearchError
from nexus.research.nodes.common import failed, stopped, warnings_with
from nexus.research.routing import assign_evidence_ids, route_decision
from nexus.research.state import ResearchState
from nexus.schemas.report import ReportSection, ReportStatus, ResearchReport

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext

INSUFFICIENT_SUMMARY = "No sources were retrieved, so this question is not answered."


class StaticSynthesizer:
    """Deterministic report. Group B replaces this with a grounded model call."""

    async def synthesize(
        self,
        state: ResearchState,
        *,
        status: ReportStatus,
    ) -> ResearchReport:
        if status == "insufficient_evidence" or not state["evidence"]:
            return ResearchReport(
                title="Insufficient evidence",
                summary=INSUFFICIENT_SUMMARY,
                sections=[],
                limitations=["No accepted evidence was available."],
                status="insufficient_evidence",
                sources=[],
            )
        refs = assign_evidence_ids(state["evidence"])
        title = state["question"].strip()[:200]
        limitations = [] if status == "completed" else ["Coverage is incomplete."]
        return ResearchReport(
            title=title,
            summary="Retrieved evidence is listed in the sources.",
            sections=[
                ReportSection(
                    heading="Evidence",
                    content="See cited sources.",
                    citations=[ref.evidence_id for ref in refs],
                )
            ],
            limitations=limitations,
            status=status,
            sources=refs,
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
    try:
        report = await runtime.context.synthesizer.synthesize(
            state,
            status=decision.report_status,
        )
    except asyncio.CancelledError:
        raise
    except ResearchError as exc:
        return failed(exc.code)
    except Exception:
        return failed("invalid_model_output")
    if report.status != decision.report_status:
        return failed("invalid_model_output")
    warnings = list(state["warnings"])
    if decision.warning is not None:
        warnings = warnings_with(state, decision.warning)
    return {
        "status": report.status,
        "report": report.model_dump(mode="json"),
        "error_code": None,
        "warnings": warnings,
    }
