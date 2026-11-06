"""Behavioral checks for evidence analysis, adaptive search and verified reports."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from langchain_core.messages import BaseMessage
from tests.conftest import make_settings
from tests.unit.graph_support import (
    NOW,
    FakeDiscovery,
    FakeIngestion,
    FakePlanner,
    FakeRetrieval,
    make_context,
    make_match,
    make_state,
    plan_for,
    runtime_for,
    with_plan,
)

from nexus.ai.analysis import EvidenceAnalyzer
from nexus.ai.coverage import OpenAICoverageEvaluator
from nexus.ai.errors import ResearchError
from nexus.ai.synthesis import assemble_report_v2, enrich_refs, report_messages
from nexus.ai.verification import ReportVerifier
from nexus.ingestion.chunking import Chunker
from nexus.ingestion.web import extract_article
from nexus.research.budgets import batch_for_state, profile_limits, work_seconds
from nexus.research.nodes.analysis import analyze_node
from nexus.research.nodes.retrieval import _passage, retrieve_node
from nexus.research.nodes.verification import verify_node
from nexus.research.routing import assign_evidence_ids
from nexus.research.state import merge_update
from nexus.schemas.report import ResearchReport
from nexus.schemas.research import ResearchRunRequest
from nexus.tools.web_search import normalize_url


class Model:
    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.messages: list[list[BaseMessage]] = []

    async def ainvoke(self, messages: list[BaseMessage]) -> object:
        self.messages.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def quality_state():
    state = with_plan(make_state(), ["official statistics", "counter evidence"])
    passage = _passage(
        make_match(UUID(state["research_id"]), text="In 2025 the value was 10 kg."),
        UUID(state["research_id"]),
    )
    return merge_update(
        state,
        {"evidence": [passage], "applied_limits": {"depth": "deep"}, "depth": "deep"},
    )


def context():
    return make_context(
        planner=FakePlanner(plan_for(["official statistics"])),
        discovery=FakeDiscovery([]),
        ingestion=FakeIngestion(),
        retrieval=FakeRetrieval([]),
    )


def draft():
    block = {
        "kind": "fact",
        "text": "In 2025 the value was 10 kg.",
        "citations": ["E1"],
        "columns": [],
        "rows": [],
    }
    return {
        "title": "Measured value",
        "summary_blocks": [block],
        "sections": [{"heading": "Results", "task_ids": ["T1"], "blocks": [block]}],
        "limitations": [],
    }


def checks(verdict="supported"):
    return {
        "checks": [
            {
                "block_id": key,
                "verdict": verdict,
                "reason": "Compared to cited passage.",
            }
            for key in ["summary:1", "section:1:1"]
        ]
    }


def analyzed():
    return {
        "claims": [
            {
                "task_id": "T1",
                "statement": "Value was 10 kg in 2025.",
                "supporting_evidence": ["E1"],
                "contradicting_evidence": [],
                "limitations": ["Only the 2025 period."],
            }
        ],
        "covered_task_ids": ["T1"],
        "missing_task_ids": [],
        "gaps": [],
        "next_queries": [],
    }


@pytest.mark.parametrize(
    "depth,seconds,rounds,pages,passages,calls",
    [
        ("standard", 180, 2, 8, 24, 20),
        ("deep", 600, 4, 20, 50, 40),
    ],
)
def test_server_profiles(depth, seconds, rounds, pages, passages, calls):
    limits = profile_limits(make_settings(), depth)
    assert (
        limits.deadline_seconds,
        limits.max_iterations,
        limits.max_web_pages,
        limits.max_passages,
        limits.max_llm_calls,
    ) == (seconds, rounds, pages, passages, calls)
    assert limits.max_search_calls == rounds * 3
    assert limits.excerpt_chars == 2000
    assert (
        ResearchRunRequest(question="A sufficiently long question").depth == "standard"
    )


async def test_analysis_exposes_comparability_and_is_used_for_coverage():
    state = quality_state()
    model = Model(analyzed())
    analysis = await EvidenceAnalyzer(make_settings(), model).analyze(state)
    state = merge_update(state, {"analysis": analysis.model_dump()})
    unused = Model(AssertionError("coverage should reuse analysis"))
    outcome = await OpenAICoverageEvaluator(make_settings(), unused).evaluate(state)
    assert outcome.coverage["sufficient"]
    assert outcome.llm_calls == 0
    assert not unused.messages
    assert "units" in model.messages[0][0].content


@pytest.mark.parametrize(
    "mutation", ["unknown_evidence", "unknown_task", "unearned_coverage"]
)
async def test_analysis_rejects_invalid_grounding(mutation):
    payload = analyzed()
    if mutation == "unknown_evidence":
        payload["claims"][0]["supporting_evidence"] = ["E999"]
    elif mutation == "unknown_task":
        payload["claims"][0]["task_id"] = "T9"
    else:
        payload["claims"] = []
    with pytest.raises(ValueError):
        await EvidenceAnalyzer(make_settings(), Model(payload)).analyze(quality_state())


async def test_analysis_failure_cannot_become_sufficient():
    state = quality_state()
    ctx = replace(
        context(), analyzer=EvidenceAnalyzer(make_settings(), Model(ValueError("bad")))
    )
    update = await analyze_node(state, runtime_for(ctx))
    assert "analysis_failed" in update["warnings"]
    state = merge_update(state, update)
    outcome = await OpenAICoverageEvaluator(make_settings()).evaluate(state)
    assert not outcome.coverage["sufficient"]
    assert outcome.coverage["missing_task_ids"] == ["T1"]


def test_refinement_queries_come_from_gaps_and_skip_used_queries():
    state = quality_state()
    analysis = analyzed()
    analysis.update(
        covered_task_ids=[],
        missing_task_ids=["T1"],
        gaps=["Need contradictory data."],
        next_queries=["official statistics", "2025 measurement methodology"],
    )
    state = merge_update(
        state,
        {
            "analysis": analysis,
            "query_batches": [["official statistics"]],
            "iteration": 1,
        },
    )
    assert batch_for_state(state, profile_limits(make_settings(), "deep")) == [
        "2025 measurement methodology"
    ]


def test_time_reserve_stops_collection_before_final_deadline():
    state = quality_state()
    limits = profile_limits(make_settings(), "deep")
    assert work_seconds(state, limits, NOW + timedelta(seconds=450)) == 0
    assert work_seconds(state, limits, NOW + timedelta(seconds=440)) == 10


async def test_retrieval_gives_each_subquestion_evidence_and_deduplicates_reprints():
    state = with_plan(quality_state(), ["one", "two", "three", "four"])
    scope = UUID(state["research_id"])
    first = make_match(scope, text="First subject", score=0.99)
    duplicate = make_match(scope, text="First subject", score=0.98)
    second = make_match(scope, text="Second subject", score=0.1)

    class Retrieval:
        async def search(self, research_id, query, top_k, *, owner_id):
            return [first, duplicate] if "group 0" in query else [second]

    ctx = replace(
        context(),
        retrieval=Retrieval(),
        limits=replace(context().limits, max_passages=2),
    )
    result = await retrieve_node(state, runtime_for(ctx))
    assert {p["excerpt"] for p in result["evidence"]} == {
        "First subject",
        "Second subject",
    }


def test_report_prompt_contains_plan_analysis_and_provenance():
    state = quality_state()
    refs = enrich_refs(state, assign_evidence_ids(state["evidence"]))
    payload = json.loads(report_messages(state, refs)[1].content)
    assert payload["plan"] == state["plan"]
    assert payload["as_of"] == state["started_at"]
    assert payload["evidence"][0]["excerpt"] == "In 2025 the value was 10 kg."


async def test_completed_requires_both_coverage_and_support_checks():
    state = quality_state()
    refs = enrich_refs(state, assign_evidence_ids(state["evidence"]))
    report = assemble_report_v2(draft(), status="completed", snapshot=refs)
    assert report.status == "partial"
    state = merge_update(
        state,
        {
            "report": report.model_dump(),
            "status": "partial",
            "coverage": {
                "sufficient": True,
                "covered_task_ids": ["T1"],
                "missing_task_ids": [],
                "gaps": [],
                "uncertainty": "",
            },
        },
    )
    ctx = replace(context(), verifier=ReportVerifier(make_settings(), Model(checks())))
    result = await verify_node(state, runtime_for(ctx))
    assert result["status"] == "completed"
    assert result["report"]["verification"] == "passed"
    assert result["usage"]["llm_calls"] == 1


@pytest.mark.parametrize(
    "verdict", ["unsupported", "contradictory", "insufficient_context"]
)
async def test_failed_claims_get_one_repair_and_are_removed_if_still_unsupported(
    verdict,
):
    state = quality_state()
    report = assemble_report_v2(
        draft(),
        status="completed",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    state = merge_update(state, {"report": report.model_dump(), "status": "partial"})
    repair = Model(draft())
    verifier = ReportVerifier(
        make_settings(), Model(checks(verdict), checks(verdict)), repair
    )
    result = await verify_node(
        state, runtime_for(replace(context(), verifier=verifier))
    )
    assert result["status"] == "partial"
    assert result["report"]["verification"] == "incomplete"
    assert not result["report"]["summary_blocks"]
    assert result["report"]["body_gaps"] == ["T1"]
    assert all(
        b["kind"] == "context"
        for s in result["report"]["sections"]
        for b in s["blocks"]
    )
    assert len(repair.messages) == 1
    assert result["usage"]["llm_calls"] == 3


async def test_verification_failure_preserves_partial_report():
    state = quality_state()
    report = assemble_report_v2(
        draft(),
        status="completed",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    state = merge_update(state, {"report": report.model_dump(), "status": "partial"})
    ctx = replace(
        context(),
        verifier=ReportVerifier(make_settings(), Model(ValueError("offline"))),
    )
    result = await verify_node(state, runtime_for(ctx))
    assert result["status"] == "partial"
    assert "verification_failed" in result["warnings"]
    assert result["report"]["sections"]


async def test_checker_must_cover_every_block_and_cancellation_propagates():
    state = quality_state()
    report = assemble_report_v2(
        draft(),
        status="completed",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    with pytest.raises(ValueError):
        await ReportVerifier(make_settings(), Model({"checks": []})).check(report)
    state = merge_update(state, {"report": report.model_dump(), "status": "partial"})
    ctx = replace(
        context(),
        verifier=ReportVerifier(make_settings(), Model(asyncio.CancelledError())),
    )
    with pytest.raises(asyncio.CancelledError):
        await verify_node(state, runtime_for(ctx))


def test_v2_schema_does_not_accept_unverified_completion_or_foreign_citations():
    state = quality_state()
    report = assemble_report_v2(
        draft(),
        status="completed",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    raw = report.model_dump()
    raw["status"] = "completed"
    with pytest.raises(ValueError):
        ResearchReport.model_validate(raw)
    payload = draft()
    payload["summary_blocks"][0]["citations"] = ["E999"]
    with pytest.raises(ResearchError):
        assemble_report_v2(payload, status="partial", snapshot=report.sources)


def test_tracking_parameters_do_not_create_independent_sources():
    assert (
        normalize_url("https://example.org/a?utm_source=x&year=2025#ref")
        == "https://example.org/a?year=2025"
    )


def test_html_tables_and_headings_survive_extraction_and_chunking():
    body = b"""<html><head><title>Measurements</title></head><body><article>
    <h1>Annual measurements</h1><p>This report contains measurements from repeated
    observations over the calendar year. The table gives measured values and units.</p>
    <h2>Results in kilograms</h2><table><tr><th>Year</th><th>Mass (kg)</th></tr>
    <tr><td>2025</td><td>10</td></tr></table><p>These measurements apply only to the
    sampled population and cannot be extrapolated to other periods or groups.</p>
    </article></body></html>"""
    text, _ = extract_article(body, "https://example.org/report")
    assert "2025" in text and "Mass (kg)" in text
    chunker = Chunker(
        chunk_tokens=60,
        overlap_tokens=10,
        max_chunks=20,
        model_name="text-embedding-3-small",
    )
    chunks = chunker.chunk_article(text)
    assert any("2025" in c.text and "kilograms" in c.text for c in chunks)


async def test_deep_graph_runs_four_adaptive_rounds_then_verifies_partial_report():
    from tests.unit.graph_support import discovered

    from nexus.ai.synthesis import OpenAIReportSynthesizer
    from nexus.research.graph import build_graph, run_config

    state = quality_state()
    state = merge_update(state, {"evidence": [], "plan": None})
    replies = []
    for round_no in range(4):
        reply = analyzed()
        reply.update(
            covered_task_ids=[],
            missing_task_ids=["T1"],
            gaps=["Need an independently measured comparison."],
            next_queries=[f"independent measurement {round_no}-{i}" for i in range(3)],
        )
        replies.append(reply)
    ctx = replace(
        context(),
        limits=profile_limits(make_settings(), "deep"),
        discovery=FakeDiscovery(
            [
                discovered(f"https://example.org/{i}", search_calls=3, llm_calls=1)
                for i in range(4)
            ]
        ),
        retrieval=FakeRetrieval([make_match(UUID(state["research_id"]))]),
        analyzer=EvidenceAnalyzer(make_settings(), Model(*replies)),
        evaluator=OpenAICoverageEvaluator(make_settings()),
        synthesizer=OpenAIReportSynthesizer(make_settings(), Model(draft())),
        verifier=ReportVerifier(make_settings(), Model(checks())),
    )
    result = await build_graph().ainvoke(
        state, config=run_config(state["run_id"]), context=ctx
    )
    assert result["iteration"] == 4
    assert result["usage"]["search_calls"] == 12
    assert result["usage"]["llm_calls"] == 11
    assert result["status"] == "partial"
    assert result["report"]["verification"] == "passed"
    assert len({q for batch in result["query_batches"] for q in batch}) == 10


async def test_v2_writer_can_retry_a_timeout_within_the_logical_call_cap():
    from nexus.ai.errors import provider_timeout
    from nexus.ai.synthesis import OpenAIReportSynthesizer

    model = Model(provider_timeout(), draft())
    outcome = await OpenAIReportSynthesizer(make_settings(), model).synthesize(
        quality_state(),
        status="partial",
        max_calls=2,
    )
    assert outcome.report is not None and outcome.report.format_version == 2
    assert outcome.llm_calls == 2
    assert len(model.messages) == 2


def test_methodology_only_section_is_checked_as_context_not_rejected_as_uncited():
    state = quality_state()
    payload = draft()
    payload["sections"].insert(
        0,
        {
            "heading": "Method",
            "blocks": [
                {
                    "kind": "context",
                    "text": "This report compares only the retrieved evidence.",
                    "citations": [],
                    "columns": [],
                    "rows": [],
                }
            ],
        },
    )
    report = assemble_report_v2(
        payload,
        status="partial",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    from nexus.ai.verification import report_blocks

    assert report.sections[0].citations == []
    assert report_blocks(report)["section:1:1"].kind == "context"


async def test_supported_blocks_cannot_complete_a_report_that_omits_a_planned_task():
    state = quality_state()
    report = assemble_report_v2(
        draft(),
        status="completed",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    state = merge_update(
        state,
        {
            "report": report.model_dump(),
            "status": "partial",
            "coverage": {
                "sufficient": True,
                "covered_task_ids": ["T1"],
                "missing_task_ids": [],
                "gaps": [],
                "uncertainty": "",
            },
        },
    )
    incomplete = {**checks(), "missing_task_ids": ["T1"]}
    verifier = ReportVerifier(
        make_settings(), Model(incomplete, incomplete), Model(draft())
    )
    result = await verify_node(
        state, runtime_for(replace(context(), verifier=verifier))
    )
    assert result["status"] == "partial"
    assert result["report"]["verification"] == "incomplete"
    assert any("T1" in item for item in result["report"]["limitations"])


async def test_recheck_failure_keeps_checked_draft_and_removes_known_bad_claims():
    state = quality_state()
    report = assemble_report_v2(
        draft(),
        status="partial",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    state = merge_update(state, {"report": report.model_dump(), "status": "partial"})
    original_checks = checks()
    original_checks["checks"][1]["verdict"] = "unsupported"
    unverified_repair = draft()
    unverified_repair["summary_blocks"][0]["text"] = "The value was 999 kg."
    verifier = ReportVerifier(
        make_settings(),
        Model(original_checks, ValueError("recheck unavailable")),
        Model(unverified_repair),
    )
    result = await verify_node(
        state, runtime_for(replace(context(), verifier=verifier))
    )
    assert result["status"] == "partial"
    assert result["report"]["summary"] == "In 2025 the value was 10 kg."
    assert result["report"]["body_gaps"] == ["T1"]
    assert all(
        b["kind"] == "context"
        for s in result["report"]["sections"]
        for b in s["blocks"]
    )
    assert "The value was 999 kg." not in json.dumps(result["report"])
    assert result["usage"]["llm_calls"] == 3


def summary_only_draft():
    payload = draft()
    payload["sections"] = [
        {
            "heading": "Scope and Method",
            "task_ids": [],
            "blocks": [
                {
                    "kind": "context",
                    "text": "This report reviews the supplied evidence.",
                    "citations": [],
                    "columns": [],
                    "rows": [],
                }
            ],
        }
    ]
    return payload


async def test_writer_marks_summary_with_only_methodology_as_incomplete():
    from nexus.ai.synthesis import OpenAIReportSynthesizer

    outcome = await OpenAIReportSynthesizer(
        make_settings(), Model(summary_only_draft())
    ).synthesize(
        quality_state(),
        status="completed",
        max_calls=2,
    )
    assert outcome.report is not None
    assert outcome.warning == "report_body_incomplete"
    assert outcome.report.body_gaps == ["T1"]
    assert outcome.report.status == "partial"
    assert len(outcome.report.sections) == 2
    assert outcome.report.sections[1].task_ids == ["T1"]


async def test_summary_answers_cannot_replace_report_body_even_if_checker_agrees():
    state = quality_state()
    report = assemble_report_v2(
        summary_only_draft(),
        status="completed",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    state = merge_update(
        state,
        {
            "report": report.model_dump(),
            "status": "partial",
            "coverage": {
                "sufficient": True,
                "covered_task_ids": ["T1"],
                "missing_task_ids": [],
                "gaps": [],
                "uncertainty": "",
            },
        },
    )
    verifier = ReportVerifier(
        make_settings(), Model(checks(), checks()), Model(summary_only_draft())
    )
    result = await verify_node(
        state, runtime_for(replace(context(), verifier=verifier))
    )
    assert result["status"] == "partial"
    assert result["report"]["verification"] == "incomplete"
    assert result["report"]["body_gaps"] == ["T1"]
    assert "report_body_incomplete" in result["warnings"]


async def test_all_task_sections_survive_verification_and_are_required_for_completion():
    from nexus.ai.verification import report_blocks

    state = with_plan(quality_state(), [f"query {i}" for i in range(9)])
    payload = draft()
    payload["sections"] = [
        {
            "heading": f"Findings for task {i}",
            "task_ids": [f"T{i}"],
            "blocks": [draft()["summary_blocks"][0]],
        }
        for i in range(1, 4)
    ]
    report = assemble_report_v2(
        payload,
        status="completed",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    state = merge_update(
        state,
        {
            "report": report.model_dump(),
            "status": "partial",
            "coverage": {
                "sufficient": True,
                "covered_task_ids": ["T1", "T2", "T3"],
                "missing_task_ids": [],
                "gaps": [],
                "uncertainty": "",
            },
        },
    )
    checked = {
        "checks": [
            {"block_id": block_id, "verdict": "supported", "reason": "Supported."}
            for block_id in report_blocks(report)
        ],
        "missing_task_ids": [],
    }
    verifier = ReportVerifier(make_settings(), Model(checked))
    result = await verify_node(
        state, runtime_for(replace(context(), verifier=verifier))
    )
    assert result["status"] == "completed"
    assert len(result["report"]["sections"]) == 3
    assert [s["task_ids"] for s in result["report"]["sections"]] == [
        ["T1"],
        ["T2"],
        ["T3"],
    ]
    assert result["report"]["body_gaps"] == []


@pytest.mark.parametrize("max_calls,missing", [(3, []), (2, ["T3"])])
async def test_writer_completes_missing_sections_without_rewriting_valid_body(
    max_calls, missing
):
    from nexus.ai.synthesis import OpenAIReportSynthesizer

    state = with_plan(quality_state(), [f"query {i}" for i in range(7)])
    sections = Model(
        {"heading": "Second task", "blocks": draft()["sections"][0]["blocks"]},
        {"heading": "Third task", "blocks": draft()["sections"][0]["blocks"]},
    )
    outcome = await OpenAIReportSynthesizer(
        make_settings(), Model(draft()), section_model=sections
    ).synthesize(state, status="completed", max_calls=max_calls)
    assert outcome.llm_calls == max_calls
    assert outcome.report is not None
    assert outcome.report.body_gaps == missing
    assert outcome.report.sections[0].heading == "Results"
    assert outcome.report.sections[0].task_ids == ["T1"]
    assert outcome.report.sections[1].task_ids == ["T2"]
    assert len(sections.messages) == max_calls - 1


def test_profile_reserves_section_completion_within_global_budget():
    from nexus.research.budgets import (
        charge_synthesis,
        synthesis_calls_allowed,
    )

    limits = profile_limits(make_settings(), "deep")
    from nexus.research.state import empty_usage

    usage = empty_usage()
    assert synthesis_calls_allowed(usage, limits) == 5
    charged = charge_synthesis(usage, limits, calls=5)
    assert charged["llm_calls"] == 5
    assert synthesis_calls_allowed(charged, limits) == 0


async def test_missing_section_retries_malformed_output_within_budget():
    from nexus.ai.synthesis import OpenAIReportSynthesizer

    sections = Model(ValueError("malformed section"), draft()["sections"][0])
    outcome = await OpenAIReportSynthesizer(
        make_settings(), Model(summary_only_draft()), section_model=sections
    ).synthesize(quality_state(), status="partial", max_calls=3)
    assert outcome.llm_calls == 3
    assert outcome.report is not None
    assert outcome.report.body_gaps == []
    assert outcome.warning is None


async def test_repair_cannot_remove_existing_analytical_sections():
    state = quality_state()
    report = assemble_report_v2(
        draft(),
        status="partial",
        snapshot=enrich_refs(state, assign_evidence_ids(state["evidence"])),
    )
    verifier = ReportVerifier(make_settings(), repair_model=Model(summary_only_draft()))
    with pytest.raises(ValueError, match="removed analytical task sections"):
        await verifier.repair(
            state,
            report,
            await ReportVerifier(make_settings(), Model(checks())).check(report),
        )
