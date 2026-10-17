"""Routes stay finite. Coverage is advice; budgets and progress decide."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tests.conftest import make_settings

from nexus.research.budgets import (
    charge_discovery,
    charge_pages,
    charge_planner,
    limits_from_settings,
)
from nexus.research.routing import (
    BUDGET_EXHAUSTED,
    DEADLINE_EXCEEDED,
    DUPLICATE_QUERY_BATCH,
    NO_PROGRESS,
    assign_evidence_ids,
    decide,
    route_after_plan,
    route_decision,
)
from nexus.research.state import (
    EvidenceState,
    ResearchState,
    empty_usage,
    initial_state,
    merge_update,
)
from nexus.schemas.research import ResearchPlan, ResearchTask

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
_COVERAGE_GAP = {
    "sufficient": False,
    "covered_task_ids": [],
    "missing_task_ids": [],
    "gaps": [],
    "uncertainty": "",
}


def _limits():
    return limits_from_settings(make_settings())


def _plan(*queries: str) -> ResearchPlan:
    return ResearchPlan(
        objective="Find the practical limits.",
        tasks=[
            ResearchTask(
                question="Which limits are enforced?",
                search_queries=list(queries),
            )
        ],
    )


def _state() -> ResearchState:
    return initial_state(
        research_id=uuid4(),
        run_id=uuid4(),
        question="How do bounded research graphs stop?",
        started_at=NOW,
    )


def _with_plan(queries: list[str]) -> ResearchState:
    plan = _plan(*queries)
    return merge_update(_state(), {"plan": plan.model_dump(mode="json")})


def _passage(
    state: ResearchState, chunk_id: str, *, score: float = 0.8
) -> EvidenceState:
    return {
        "chunk_id": chunk_id,
        "source_id": str(uuid4()),
        "research_id": state["research_id"],
        "url": "https://example.org/notes",
        "page_number": None,
        "score": score,
        "excerpt": "A short excerpt.",
    }


def test_sufficient_coverage_synthesizes_even_when_budget_remains() -> None:
    state = _with_plan(["alpha", "beta", "gamma"])
    usage = charge_planner(empty_usage(), _limits())
    chunk_id = str(uuid4())
    state = merge_update(
        state,
        {
            "iteration": 1,
            "usage": usage,
            "query_batches": [["alpha"]],
            "evidence": [_passage(state, chunk_id)],
            "iteration_progress": {"pages_indexed": 0, "chunk_ids": []},
            "coverage": {**_COVERAGE_GAP, "sufficient": True},
        },
    )

    decision = route_decision(state, _limits(), NOW)

    assert decision.route == "synthesize"
    assert decision.report_status == "completed"
    assert decide(state, _limits(), NOW) == "synthesize"


def test_one_refinement_is_allowed_and_a_second_is_not() -> None:
    limits = _limits()
    plan = ResearchPlan(
        objective="Find the practical limits.",
        tasks=[
            ResearchTask(
                question="Which limits are enforced first?",
                search_queries=["alpha one", "alpha two", "alpha three"],
            ),
            ResearchTask(
                question="Which limits are enforced next?",
                search_queries=["beta one", "beta two", "beta three"],
            ),
        ],
    )
    state = merge_update(_state(), {"plan": plan.model_dump(mode="json")})
    usage = charge_discovery(
        charge_planner(empty_usage(), limits),
        limits,
        search_calls=3,
        llm_calls=4,
    )
    usage = charge_pages(usage, limits, indexed=1)
    chunk_id = str(uuid4())
    state = merge_update(
        state,
        {
            "iteration": 1,
            "usage": usage,
            "query_batches": [["alpha one", "alpha two", "alpha three"]],
            "evidence": [_passage(state, chunk_id)],
            "iteration_progress": {"pages_indexed": 0, "chunk_ids": []},
            "coverage": _COVERAGE_GAP,
        },
    )

    assert route_decision(state, limits, NOW).route == "continue_discovery"

    stopped = merge_update(
        state,
        {
            "iteration": 2,
            "query_batches": [
                ["alpha one", "alpha two", "alpha three"],
                ["beta one", "beta two", "beta three"],
            ],
            "usage": charge_discovery(usage, limits, search_calls=3, llm_calls=4),
        },
    )
    decision = route_decision(stopped, limits, NOW)

    assert decision.route == "synthesize"
    assert decision.report_status == "partial"
    assert decision.warning == BUDGET_EXHAUSTED


def test_identical_query_batch_stops() -> None:
    limits = _limits()
    state = _with_plan(["Alpha"])
    usage = charge_pages(charge_planner(empty_usage(), limits), limits, indexed=1)
    chunk_id = str(uuid4())
    state = merge_update(
        state,
        {
            "iteration": 1,
            "usage": usage,
            "query_batches": [["alpha"]],
            "evidence": [_passage(state, chunk_id)],
            "iteration_progress": {"pages_indexed": 0, "chunk_ids": []},
            "coverage": _COVERAGE_GAP,
        },
    )

    decision = route_decision(state, limits, NOW)

    assert decision.route == "synthesize"
    assert decision.warning == DUPLICATE_QUERY_BATCH


def test_no_new_pages_or_chunks_stops() -> None:
    limits = _limits()
    state = _with_plan(["alpha", "beta"])
    usage = charge_planner(empty_usage(), limits)
    state = merge_update(
        state,
        {
            "iteration": 1,
            "usage": usage,
            "query_batches": [["alpha"]],
            "iteration_progress": {"pages_indexed": 0, "chunk_ids": []},
            "coverage": _COVERAGE_GAP,
        },
    )

    decision = route_decision(state, limits, NOW)

    assert decision.route == "insufficient_evidence"
    assert decision.report_status == "insufficient_evidence"
    assert decision.warning == NO_PROGRESS


def test_deadline_with_evidence_is_partial_even_when_coverage_says_sufficient() -> None:
    limits = _limits()
    state = _with_plan(["alpha", "beta"])
    chunk_id = str(uuid4())
    state = merge_update(
        state,
        {
            "started_at": (NOW - timedelta(seconds=180)).isoformat(),
            "iteration": 1,
            "usage": charge_planner(empty_usage(), limits),
            "query_batches": [["alpha"]],
            "evidence": [_passage(state, chunk_id)],
            "coverage": {**_COVERAGE_GAP, "sufficient": True},
        },
    )

    decision = route_decision(state, limits, NOW)

    assert decision.route == "synthesize"
    assert decision.report_status == "partial"
    assert decision.warning == DEADLINE_EXCEEDED


def test_deadline_without_evidence_is_insufficient() -> None:
    state = merge_update(
        _with_plan(["alpha"]),
        {"started_at": (NOW - timedelta(seconds=180)).isoformat()},
    )

    decision = route_decision(state, _limits(), NOW)

    assert decision.route == "insufficient_evidence"
    assert decision.report_status == "insufficient_evidence"


def test_malformed_state_fails_without_raising() -> None:
    state = _state()
    broken = dict(state)
    broken["schema_version"] = 2
    negative = merge_update(state, {})
    negative = dict(negative)
    negative["usage"] = dict(negative["usage"])
    negative["usage"]["search_calls"] = -1

    assert decide(broken, _limits(), NOW) == "fail"  # type: ignore[arg-type]
    assert decide(negative, _limits(), NOW) == "fail"  # type: ignore[arg-type]


def test_planner_failure_does_not_discover() -> None:
    waiting = _state()
    planned = _with_plan(["alpha"])
    failed = merge_update(
        waiting,
        {
            "status": "failed",
            "error_code": "provider_not_configured",
            "report": None,
        },
    )

    assert route_after_plan(waiting) == "fail"
    assert route_after_plan(planned) == "discover"
    assert route_after_plan(failed) == "fail"
    assert decide(failed, _limits(), NOW) == "fail"


def test_evidence_ids_follow_score_then_source_then_chunk() -> None:
    research_id = str(uuid4())
    low = "00000000-0000-4000-8000-000000000002"
    high = "00000000-0000-4000-8000-00000000000a"
    passages: list[EvidenceState] = [
        {
            "chunk_id": high,
            "source_id": high,
            "research_id": research_id,
            "url": "https://example.org/a",
            "page_number": None,
            "score": 0.2,
            "excerpt": "Low score.",
        },
        {
            "chunk_id": high,
            "source_id": low,
            "research_id": research_id,
            "url": None,
            "page_number": 3,
            "score": 0.2,
            "excerpt": "Same score, earlier source.",
        },
        {
            "chunk_id": low,
            "source_id": high,
            "research_id": research_id,
            "url": "https://example.org/b",
            "page_number": None,
            "score": 0.9,
            "excerpt": "Best score.",
        },
    ]

    refs = assign_evidence_ids(passages)

    assert [item.evidence_id for item in refs] == ["E1", "E2", "E3"]
    assert refs[0].chunk_id == low
    assert refs[0].page_number is None
    assert refs[1].source_id == low
    assert refs[1].page_number == 3
    assert refs[2].source_id == high
    assert refs[0].url == "https://example.org/b"


def _two_task_plan() -> ResearchPlan:
    return ResearchPlan(
        objective="Find the practical limits.",
        tasks=[
            ResearchTask(
                question="Which limits are enforced first?",
                search_queries=["alpha"],
            ),
            ResearchTask(
                question="Which limits are enforced next?",
                search_queries=["beta"],
            ),
        ],
    )


def _after_alpha(state: ResearchState, *, iteration: int) -> ResearchState:
    limits = _limits()
    usage = charge_pages(charge_planner(empty_usage(), limits), limits, indexed=1)
    chunk_id = str(uuid4())
    return merge_update(
        state,
        {
            "iteration": iteration,
            "usage": usage,
            "query_batches": [["alpha"]],
            "evidence": [_passage(state, chunk_id)],
            "iteration_progress": {"pages_indexed": 0, "chunk_ids": []},
        },
    )


def test_already_sent_missing_task_queries_stop() -> None:
    state = merge_update(_state(), {"plan": _two_task_plan().model_dump(mode="json")})
    state = _after_alpha(state, iteration=1)
    state = merge_update(
        state,
        {
            "coverage": {
                **_COVERAGE_GAP,
                "covered_task_ids": ["T2"],
                "missing_task_ids": ["T1"],
            }
        },
    )

    decision = route_decision(state, _limits(), NOW)

    assert decision.route == "synthesize"
    assert decision.report_status == "partial"
    assert decision.warning == DUPLICATE_QUERY_BATCH


def test_exhausted_budget_stops_with_fresh_missing_queries() -> None:
    state = merge_update(_state(), {"plan": _two_task_plan().model_dump(mode="json")})
    state = _after_alpha(state, iteration=2)
    state = merge_update(
        state,
        {"coverage": {**_COVERAGE_GAP, "missing_task_ids": ["T2"]}},
    )

    decision = route_decision(state, _limits(), NOW)

    assert decision.route == "synthesize"
    assert decision.report_status == "partial"
    assert decision.warning == BUDGET_EXHAUSTED


def test_no_new_passage_stops_with_fresh_missing_queries() -> None:
    limits = _limits()
    state = merge_update(_state(), {"plan": _two_task_plan().model_dump(mode="json")})
    usage = charge_pages(charge_planner(empty_usage(), limits), limits, indexed=1)
    chunk_id = str(uuid4())
    state = merge_update(
        state,
        {
            "iteration": 1,
            "usage": usage,
            "query_batches": [["alpha"]],
            "evidence": [_passage(state, chunk_id)],
            "iteration_progress": {"pages_indexed": 1, "chunk_ids": [chunk_id]},
            "coverage": {**_COVERAGE_GAP, "missing_task_ids": ["T2"]},
        },
    )

    decision = route_decision(state, limits, NOW)

    assert decision.route == "synthesize"
    assert decision.report_status == "partial"
    assert decision.warning == NO_PROGRESS
