"""One verification, at most one repair, and a final verification."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from nexus.ai.report_body import explain_body_gaps
from nexus.ai.verification import report_blocks
from nexus.research.nodes.common import before_provider, warnings_with
from nexus.research.state import ResearchState, copy_usage
from nexus.schemas.analysis import ReportVerification
from nexus.schemas.report import ResearchReport

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext


async def verify_node(
    state: ResearchState, runtime: Runtime[GraphContext]
) -> dict[str, object]:
    gate = await before_provider(state, runtime.context.session_active)
    if gate is not None:
        return gate
    verifier = runtime.context.verifier
    if verifier is None or state["report"] is None:
        return {}
    report = ResearchReport.model_validate(state["report"])
    if report.status == "insufficient_evidence":
        return {}
    usage = copy_usage(state["usage"])
    calls = 0
    warning = "verification_failed"
    passed = False
    checked: ReportVerification | None = None

    def seconds_left() -> float:
        elapsed = (
            runtime.context.clock() - datetime.fromisoformat(state["started_at"])
        ).total_seconds()
        return max(0, runtime.context.limits.deadline_seconds - elapsed - 1)

    def spend() -> None:
        nonlocal calls
        if usage["llm_calls"] >= runtime.context.limits.max_llm_calls:
            raise ValueError("verification budget exhausted")
        usage["llm_calls"] += 1
        calls += 1

    try:
        if report.format_version != 2:
            raise ValueError("draft is not checkable")
        spend()
        async with asyncio.timeout(seconds_left()):
            checked = await verifier.check(report, state["analysis"], state["plan"])
        passed = not checked.missing_task_ids and all(
            c.verdict == "supported" for c in checked.checks
        )
        if not passed:
            warning = "unsupported_report_claims"
            if (
                usage["llm_calls"] + 2 <= runtime.context.limits.max_llm_calls
                and seconds_left() > 25
            ):
                if not await runtime.context.session_active():
                    return {
                        "status": "interrupted",
                        "report": None,
                        "error_code": "interrupted",
                        "usage": usage,
                    }
                spend()
                async with asyncio.timeout(max(0, seconds_left() - 20)):
                    repaired = await verifier.repair(state, report, checked)
                spend()
                async with asyncio.timeout(seconds_left()):
                    rechecked = await verifier.check(
                        repaired, state["analysis"], state["plan"]
                    )
                # Keep the checked draft and its verdicts if rechecking fails.
                # Known unsupported claims can then still be removed below.
                report, checked = repaired, rechecked
                passed = not checked.missing_task_ids and all(
                    c.verdict == "supported" for c in checked.checks
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        warning = "verification_failed"
    coverage = state["coverage"]
    # Repair can remove answers; conservatively keep repaired reports partial.
    complete = passed and calls == 1 and coverage is not None and coverage["sufficient"]
    if not passed and checked is not None:
        failed_ids = {c.block_id for c in checked.checks if c.verdict != "supported"}
        blocks = report_blocks(report)
        if failed_ids <= set(blocks):
            summary_blocks = [
                b
                for i, b in enumerate(report.summary_blocks, 1)
                if f"summary:{i}" not in failed_ids
            ]
            sections = []
            for i, section in enumerate(report.sections, 1):
                kept = [
                    b
                    for j, b in enumerate(section.blocks, 1)
                    if f"section:{i}:{j}" not in failed_ids
                ]
                if kept:
                    sections.append(
                        section.model_copy(
                            update={
                                "blocks": kept,
                                "content": "\n\n".join(b.text for b in kept) or "Table",
                                "citations": list(
                                    dict.fromkeys(c for b in kept for c in b.citations)
                                ),
                            }
                        )
                    )
            report = report.model_copy(
                update={
                    "summary_blocks": summary_blocks,
                    "sections": sections,
                    "summary": "\n\n".join(b.text for b in summary_blocks)
                    or "No summary claims passed support checks.",
                }
            )
    report = report.model_copy(
        update={
            "status": "completed" if complete else "partial",
            "verification": "passed" if passed else "incomplete",
        }
    )
    if not passed:
        report = report.model_copy(
            update={
                "limitations": [
                    *report.limitations[:14],
                    *(
                        [
                            "Report does not address planned tasks: "
                            + ", ".join(checked.missing_task_ids)
                        ]
                        if checked is not None and checked.missing_task_ids
                        else []
                    ),
                    *(
                        [
                            "Model support check: " + c.reason[:500]
                            for c in checked.checks
                            if c.verdict != "supported"
                        ][:3]
                        if checked is not None
                        else []
                    ),
                    "Support checking was incomplete or found unsupported claims. "
                    "The report is partial; model verification cannot guarantee truth.",
                ]
            }
        )
    elif not complete and not report.limitations:
        report = report.model_copy(
            update={
                "limitations": [
                    "Coverage is incomplete or changed during claim repair."
                ]
            }
        )
    if report.format_version == 2:
        report = explain_body_gaps(report, state["plan"])
        if report.body_gaps:
            report = report.model_copy(update={"verification": "incomplete"})
    report = ResearchReport.model_validate(report.model_dump())
    return {
        "report": report.model_dump(mode="json"),
        "status": report.status,
        "usage": usage,
        "quality_calls": state["quality_calls"] + calls,
        "warnings": list(
            dict.fromkeys(
                [
                    *(state["warnings"] if passed else warnings_with(state, warning)),
                    *(["report_body_incomplete"] if report.body_gaps else []),
                ]
            )
        ),
    }
