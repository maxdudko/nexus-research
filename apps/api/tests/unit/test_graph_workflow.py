"""The compiled graph terminates on fakes. No provider is constructed."""

import json
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import InMemorySaver
from tests.unit.graph_support import (
    FakeDiscovery,
    FakeIngestion,
    FakePlanner,
    FakeRetrieval,
    ScriptedEvaluator,
    coverage,
    discovered,
    empty_discovery,
    make_context,
    make_match,
    make_state,
    plan_for,
)

from nexus.ai.errors import fetch_failed, provider_not_configured
from nexus.research.graph import build_graph, run_config
from nexus.research.state import validate_state

SNIPPET = "SNIPPET_SHOULD_NOT_LEAK"


async def _run(
    *,
    planner: FakePlanner,
    discovery: FakeDiscovery,
    ingestion: FakeIngestion,
    retrieval: FakeRetrieval,
    evaluator: ScriptedEvaluator,
):
    state = make_state()
    context = make_context(
        planner=planner,
        discovery=discovery,
        ingestion=ingestion,
        retrieval=retrieval,
        evaluator=evaluator,
    )
    graph = build_graph(InMemorySaver())
    result = await graph.ainvoke(
        state,
        config=run_config(state["run_id"]),
        context=context,
    )
    checked = validate_state(result)
    snapshot = await graph.aget_state(run_config(state["run_id"]))
    json.dumps(snapshot.values)
    return checked, UUID(state["research_id"])


def _ids_match(
    calls: list[tuple[UUID, object, object] | tuple[UUID, str]], research_id: UUID
) -> bool:
    return all(call[0] == research_id for call in calls)


async def test_happy_path_cites_only_the_selected_passage() -> None:
    planner = FakePlanner(plan_for(["alpha limits"]))
    discovery = FakeDiscovery(
        [
            discovered(
                "https://example.org/notes",
                search_calls=1,
                llm_calls=2,
                snippet=SNIPPET,
            )
        ]
    )
    ingestion = FakeIngestion()
    retrieval = FakeRetrieval([make_match(uuid4())], adopt_scope=True)
    result, research_id = await _run(
        planner=planner,
        discovery=discovery,
        ingestion=ingestion,
        retrieval=retrieval,
        evaluator=ScriptedEvaluator([coverage(sufficient=True)]),
    )
    graph_report = result["report"]

    assert result["status"] == "completed"
    assert graph_report is not None
    assert graph_report["status"] == "completed"
    assert graph_report["sources"][0]["evidence_id"] == "E1"  # type: ignore[index]
    assert graph_report["sources"][0]["research_id"] == str(research_id)  # type: ignore[index]
    assert graph_report["sections"][0]["citations"] == ["E1"]  # type: ignore[index]
    assert SNIPPET not in json.dumps(result)
    assert ingestion.calls == [(research_id, "https://example.org/notes")]
    assert retrieval.calls[0][0] == research_id
    assert len(planner.questions) == 1


async def test_zero_evidence_is_insufficient_and_does_not_retry() -> None:
    planner = FakePlanner(
        plan_for(["alpha one", "alpha two", "alpha three", "beta one"])
    )
    discovery = FakeDiscovery([empty_discovery()])
    ingestion = FakeIngestion()
    retrieval = FakeRetrieval([])
    result, research_id = await _run(
        planner=planner,
        discovery=discovery,
        ingestion=ingestion,
        retrieval=retrieval,
        evaluator=ScriptedEvaluator([coverage(sufficient=False)]),
    )

    assert result["status"] == "insufficient_evidence"
    assert result["report"] is not None
    assert result["report"]["sources"] == []
    assert result["report"]["sections"] == []
    assert len(discovery.questions) == 1
    assert "alpha three" in discovery.questions[0]
    assert "beta one" not in discovery.questions[0]
    assert ingestion.calls == []
    assert _ids_match(retrieval.calls, research_id)  # type: ignore[arg-type]


async def test_one_refinement_then_a_partial_report() -> None:
    planner = FakePlanner(
        plan_for(
            [
                "alpha one",
                "alpha two",
                "alpha three",
                "beta one",
                "beta two",
                "beta three",
            ]
        )
    )
    discovery = FakeDiscovery(
        [
            discovered("https://example.org/alpha", search_calls=1, llm_calls=1),
            discovered("https://example.org/beta", search_calls=2, llm_calls=2),
        ]
    )
    ingestion = FakeIngestion()
    retrieval = FakeRetrieval([make_match(uuid4())], adopt_scope=True)
    result, research_id = await _run(
        planner=planner,
        discovery=discovery,
        ingestion=ingestion,
        retrieval=retrieval,
        evaluator=ScriptedEvaluator(
            [coverage(sufficient=False), coverage(sufficient=False)]
        ),
    )

    assert len(discovery.questions) == 2
    assert "alpha one" in discovery.questions[0]
    assert "beta one" in discovery.questions[1]
    assert "beta one" not in discovery.questions[0]
    assert result["iteration"] == 2
    assert result["status"] == "partial"
    assert result["report"] is not None
    assert result["usage"]["search_calls"] == 3
    assert result["usage"]["llm_calls"] == 4
    assert _ids_match(ingestion.calls, research_id)  # type: ignore[arg-type]
    assert len(ingestion.calls) == 2
    assert _ids_match(retrieval.calls, research_id)  # type: ignore[arg-type]


async def test_no_progress_stops_after_a_failed_page() -> None:
    planner = FakePlanner(plan_for(["alpha limits", "beta limits"]))
    discovery = FakeDiscovery(
        [discovered("https://example.org/broken", search_calls=1, llm_calls=1)]
    )
    ingestion = FakeIngestion([fetch_failed()])
    retrieval = FakeRetrieval([])
    result, research_id = await _run(
        planner=planner,
        discovery=discovery,
        ingestion=ingestion,
        retrieval=retrieval,
        evaluator=ScriptedEvaluator([coverage(sufficient=False)]),
    )

    assert len(discovery.questions) == 1
    assert result["status"] == "insufficient_evidence"
    assert result["report"] is not None
    assert "page_failed" in result["warnings"]
    assert "no_progress" in result["warnings"]
    assert ingestion.calls == [(research_id, "https://example.org/broken")]


async def test_discovery_provider_failure_charges_the_ceiling() -> None:
    planner = FakePlanner(plan_for(["alpha limits"]))
    discovery = FakeDiscovery([provider_not_configured()])
    ingestion = FakeIngestion()
    retrieval = FakeRetrieval([])
    result, _research_id = await _run(
        planner=planner,
        discovery=discovery,
        ingestion=ingestion,
        retrieval=retrieval,
        evaluator=ScriptedEvaluator([]),
    )

    assert result["status"] == "insufficient_evidence"
    assert result["status"] != "completed"
    assert result["report"] is not None
    assert result["usage"]["search_calls"] == 3
    assert result["usage"]["llm_calls"] == 5
    assert "discovery_failed" in result["warnings"]
    assert len(discovery.questions) == 1
    assert ingestion.calls == []


async def test_planner_failure_does_not_produce_a_report() -> None:
    planner = FakePlanner(provider_not_configured())
    discovery = FakeDiscovery([])
    ingestion = FakeIngestion()
    retrieval = FakeRetrieval([])
    result, _research_id = await _run(
        planner=planner,
        discovery=discovery,
        ingestion=ingestion,
        retrieval=retrieval,
        evaluator=ScriptedEvaluator([]),
    )

    assert result["status"] == "failed"
    assert result["report"] is None
    assert result["error_code"] == "provider_not_configured"
    assert discovery.questions == []
    assert ingestion.calls == []
    assert retrieval.calls == []
