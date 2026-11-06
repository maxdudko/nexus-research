"""Structural report coverage, independent of the model's support judgments."""

from nexus.schemas.report import ReportBlock, ReportSection, ResearchReport
from nexus.schemas.research import ResearchPlan


def missing_body_tasks(
    report: ResearchReport,
    plan: dict[str, object] | None,
) -> list[str]:
    """Summary claims and methodological context cannot replace task findings."""
    if plan is None:
        return []
    tasks = ResearchPlan.model_validate(plan).tasks
    addressed = {
        section.task_ids[0]
        for section in report.sections
        if len(section.task_ids) == 1
        and any(block.kind != "context" and block.citations for block in section.blocks)
    }
    return [f"T{i}" for i in range(1, len(tasks) + 1) if f"T{i}" not in addressed]


def explain_body_gaps(
    report: ResearchReport,
    plan: dict[str, object] | None,
) -> ResearchReport:
    """Keep missing analysis visible instead of silently losing whole sections."""
    missing = missing_body_tasks(report, plan)
    if not missing or plan is None:
        return report.model_copy(update={"body_gaps": missing})
    tasks = ResearchPlan.model_validate(plan).tasks
    sections = list(report.sections)
    for index, task in enumerate(tasks, 1):
        task_id = f"T{index}"
        if task_id not in missing:
            continue
        text = (
            "A verified analytical section for this subquestion could not be "
            "prepared within this run. Summary statements do not substitute "
            "for the missing analysis."
        )
        gap = ReportBlock(kind="context", text=text, citations=[], columns=[], rows=[])
        matching = next(
            (i for i, section in enumerate(sections) if section.task_ids == [task_id]),
            None,
        )
        if matching is not None:
            section = sections[matching]
            if (
                not any(block.text == text for block in section.blocks)
                and len(section.blocks) < 20
                and len(section.content) + len(text) + 2 <= 30000
            ):
                sections[matching] = section.model_copy(
                    update={
                        "blocks": [*section.blocks, gap],
                        "content": section.content + "\n\n" + text,
                    }
                )
        elif len(sections) < 20:
            sections.append(
                ReportSection(
                    heading=task.question[:200],
                    task_ids=[task_id],
                    content=text,
                    blocks=[gap],
                    citations=[],
                )
            )
    return report.model_copy(
        update={"sections": sections, "body_gaps": missing, "status": "partial"}
    )
