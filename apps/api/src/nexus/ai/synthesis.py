"""Cited reports. The server assigns evidence ids and source metadata."""

import re
from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from nexus.ai.client import StructuredOutputModel
from nexus.ai.errors import ResearchError, invalid_model_output, map_provider_exception
from nexus.ai.planner import StructuredModel
from nexus.ai.prompts import SYNTHESIS_SYSTEM_PROMPT, synthesis_user_prompt
from nexus.core.config import Settings
from nexus.research.budgets import SYNTHESIS_LLM_RESERVE
from nexus.research.outcomes import SynthesisOutcome
from nexus.research.routing import assign_evidence_ids
from nexus.research.state import EvidenceState, ResearchState
from nexus.schemas.report import (
    EvidenceRef,
    ModelReport,
    ReportSection,
    ReportStatus,
    ResearchReport,
)
from nexus.tools.web_search import normalize_url

INSUFFICIENT_SUMMARY = "No sources were retrieved, so this question is not answered."
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_CITED = {"completed", "partial"}


def grounded_report(state: ResearchState, status: ReportStatus) -> ResearchReport:
    """A report that cites only retrieved passages. No model is required."""
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
    title = state["question"].strip()[:200] or "Research report"
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


def assemble_report(
    result: object,
    *,
    status: ReportStatus,
    snapshot: Sequence[EvidenceRef],
    gaps: Sequence[str] = (),
) -> ResearchReport:
    """Keep the router's status and replace every source from the snapshot."""
    if status not in _CITED:
        raise invalid_model_output()
    model = _coerce_model_report(result)
    known = {ref.evidence_id for ref in snapshot}
    allowed_urls = {ref.url for ref in snapshot if ref.url is not None}
    sections: list[ReportSection] = []
    for section in model.sections:
        citations = _dedupe(section.citations)
        if any(citation not in known for citation in citations):
            raise invalid_model_output()
        if not citations:
            raise invalid_model_output()
        sections.append(
            ReportSection(
                heading=section.heading,
                content=section.content,
                citations=citations,
            )
        )
    limitations = _limitations(model.limitations, status, gaps)
    _reject_foreign_urls(
        [model.title, model.summary, *limitations]
        + [section.heading for section in sections]
        + [section.content for section in sections],
        allowed_urls,
    )
    try:
        return ResearchReport(
            title=model.title,
            summary=model.summary,
            sections=sections,
            limitations=limitations,
            status=status,
            sources=list(snapshot),
        )
    except ValidationError:
        raise invalid_model_output() from None


class OpenAIReportSynthesizer:
    """One structured report, plus one retry when the output is ungrounded."""

    def __init__(
        self,
        settings: Settings,
        model: StructuredModel | None = None,
    ) -> None:
        self._settings = settings
        self._model = model

    async def synthesize(
        self,
        state: ResearchState,
        *,
        status: ReportStatus,
        max_calls: int,
    ) -> SynthesisOutcome:
        if not state["evidence"]:
            return SynthesisOutcome(
                report=grounded_report(state, "insufficient_evidence"),
                llm_calls=0,
            )
        if max_calls < 1:
            return SynthesisOutcome(
                report=None, llm_calls=0, warning="budget_exhausted"
            )
        passages = [_copy_passage(item) for item in state["evidence"]]
        snapshot = assign_evidence_ids(passages)
        gaps = _gaps(state)
        messages: list[BaseMessage] = [
            SystemMessage(content=SYNTHESIS_SYSTEM_PROMPT),
            HumanMessage(
                content=synthesis_user_prompt(
                    question=state["question"],
                    passages=_prompt_passages(passages, snapshot),
                    gaps=gaps,
                )
            ),
        ]
        model = self._model or StructuredOutputModel(self._settings, ModelReport)
        attempts = min(max_calls, SYNTHESIS_LLM_RESERVE)
        last_warning = "invalid_model_output"
        calls = 0
        while calls < attempts:
            calls += 1
            try:
                result = await model.ainvoke(messages)
                report = assemble_report(
                    result,
                    status=status,
                    snapshot=snapshot,
                    gaps=gaps,
                )
            except ResearchError as exc:
                if exc.code == "provider_not_configured":
                    raise
                if exc.code != "invalid_model_output":
                    return SynthesisOutcome(
                        report=None,
                        llm_calls=calls,
                        warning=exc.code,
                    )
                last_warning = exc.code
                continue
            except Exception as exc:
                mapped = map_provider_exception(exc)
                if mapped.code == "provider_not_configured":
                    raise mapped from None
                if mapped.code != "invalid_model_output":
                    return SynthesisOutcome(
                        report=None,
                        llm_calls=calls,
                        warning=mapped.code,
                    )
                last_warning = mapped.code
                continue
            return SynthesisOutcome(report=report, llm_calls=calls)
        return SynthesisOutcome(report=None, llm_calls=calls, warning=last_warning)


def _coerce_model_report(result: object) -> ModelReport:
    if isinstance(result, ModelReport):
        return result
    if isinstance(result, dict):
        try:
            return ModelReport.model_validate(result)
        except ValidationError:
            raise invalid_model_output() from None
    raise invalid_model_output()


def _dedupe(citations: list[str]) -> list[str]:
    chosen: list[str] = []
    for citation in citations:
        if citation not in chosen:
            chosen.append(citation)
    return chosen


def _limitations(
    items: list[str],
    status: ReportStatus,
    gaps: Sequence[str],
) -> list[str]:
    cleaned = [" ".join(item.split()) for item in items if item.strip()]
    if status != "partial" or cleaned:
        return cleaned
    from_gaps = [" ".join(gap.split()) for gap in gaps if gap.strip()]
    if from_gaps:
        return from_gaps[:20]
    return ["Coverage is incomplete."]


def _reject_foreign_urls(texts: list[str], allowed: set[str]) -> None:
    for text in texts:
        for match in _URL_RE.findall(text):
            normalized = normalize_url(match.rstrip(".,);]"))
            if normalized is not None and normalized not in allowed:
                raise invalid_model_output()


def _gaps(state: ResearchState) -> list[str]:
    coverage = state["coverage"]
    if coverage is None:
        return []
    return list(coverage["gaps"])


def _copy_passage(item: EvidenceState) -> EvidenceState:
    return {
        "chunk_id": item["chunk_id"],
        "source_id": item["source_id"],
        "research_id": item["research_id"],
        "url": item["url"],
        "page_number": item["page_number"],
        "score": item["score"],
        "excerpt": item["excerpt"],
    }


def _prompt_passages(
    passages: Sequence[EvidenceState],
    snapshot: Sequence[EvidenceRef],
) -> list[tuple[str, str]]:
    excerpts = {item["chunk_id"]: item["excerpt"] for item in passages}
    return [(ref.evidence_id, excerpts.get(ref.chunk_id, "")) for ref in snapshot]
