"""Node adapters stay thin and do not mutate the state they are given."""

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from tests.unit.graph_support import (
    QUESTION,
    FakeDiscovery,
    FakeIngestion,
    FakePlanner,
    FakeRetrieval,
    discovered,
    make_context,
    make_match,
    make_state,
    plan_for,
    runtime_for,
    with_plan,
)

from nexus.ai.errors import fetch_failed, provider_not_configured, search_failed
from nexus.research.nodes.discovery import discover_node
from nexus.research.nodes.ingestion import ingest_node
from nexus.research.nodes.planning import plan_node
from nexus.research.nodes.retrieval import retrieve_node
from nexus.research.state import merge_update
from nexus.retrieval.vector_store import SEARCH_LIMIT


def _context(
    *,
    planner: FakePlanner | None = None,
    discovery: FakeDiscovery | None = None,
    ingestion: FakeIngestion | None = None,
    retrieval: FakeRetrieval | None = None,
):
    return make_context(
        planner=planner or FakePlanner(plan_for(["unused query"])),
        discovery=discovery or FakeDiscovery([]),
        ingestion=ingestion or FakeIngestion(),
        retrieval=retrieval or FakeRetrieval([]),
    )


async def test_plan_node_stores_the_plan_and_charges_one_call() -> None:
    state = make_state()
    planner = FakePlanner(plan_for(["alpha limits"]))
    context = _context(planner=planner)

    update = await plan_node(state, runtime_for(context))

    assert planner.questions == [QUESTION]
    assert update["plan"]["tasks"][0]["search_queries"] == ["alpha limits"]  # type: ignore[index]
    assert update["usage"]["llm_calls"] == 1  # type: ignore[index]
    assert state["status"] == "running"
    assert state["usage"]["llm_calls"] == 0


async def test_plan_failure_has_no_report() -> None:
    state = make_state()
    context = _context(planner=FakePlanner(provider_not_configured()))

    update = await plan_node(state, runtime_for(context))

    assert update["status"] == "failed"
    assert update["report"] is None
    assert update["error_code"] == "provider_not_configured"
    assert update["usage"]["llm_calls"] == 1  # type: ignore[index]
    assert state["status"] == "running"


async def test_plan_cancellation_is_not_completed() -> None:
    state = make_state()

    class CancelPlanner:
        async def create_plan(self, question: str) -> object:
            del question
            raise asyncio.CancelledError

    context = _context(planner=CancelPlanner())  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        await plan_node(state, runtime_for(context))

    assert state["status"] == "running"
    assert state["report"] is None


async def test_discovery_charges_actual_counts_and_drops_snippets() -> None:
    state = with_plan(
        make_state(),
        ["alpha one", "alpha two", "alpha three", "beta one"],
    )
    candidates = state["candidate_urls"]
    discovery = FakeDiscovery(
        [
            discovered(
                "https://example.org/notes",
                search_calls=1,
                llm_calls=2,
                snippet="SNIPPET_SHOULD_NOT_LEAK",
            )
        ]
    )
    context = _context(discovery=discovery)

    update = await discover_node(state, runtime_for(context))

    assert "alpha one" in discovery.questions[0]
    assert "alpha three" in discovery.questions[0]
    assert "beta one" not in discovery.questions[0]
    assert update["usage"]["search_calls"] == 1  # type: ignore[index]
    assert update["usage"]["llm_calls"] == 2  # type: ignore[index]
    assert update["iteration"] == 1
    assert update["candidate_urls"] == [  # type: ignore[index]
        {"url": "https://example.org/notes", "title": "Notes"}
    ]
    assert "SNIPPET_SHOULD_NOT_LEAK" not in json.dumps(update)
    assert candidates == []
    assert state["candidate_urls"] is candidates


async def test_discovery_failure_keeps_the_run_and_reserves_the_ceiling() -> None:
    state = with_plan(make_state(), ["alpha limits"])
    discovery = FakeDiscovery([provider_not_configured()])
    context = _context(discovery=discovery)

    update = await discover_node(state, runtime_for(context))

    assert update["warnings"] == ["discovery_failed"]  # type: ignore[comparison-overlap]
    assert update["usage"]["search_calls"] == 3  # type: ignore[index]
    assert update["usage"]["llm_calls"] == 4  # type: ignore[index]
    assert "status" not in update
    assert state["status"] == "running"


async def test_ingest_uses_the_research_scope_and_survives_one_failure() -> None:
    state = make_state()
    research_id = UUID(state["research_id"])
    state = merge_update(
        state,
        {
            "candidate_urls": [
                {"url": "https://example.org/bad", "title": "Bad"},
                {"url": "https://example.org/good", "title": "Good"},
            ]
        },
    )
    ingestion = FakeIngestion([fetch_failed()])
    context = _context(ingestion=ingestion)

    update = await ingest_node(state, runtime_for(context))

    assert ingestion.calls == [
        (research_id, "https://example.org/bad"),
        (research_id, "https://example.org/good"),
    ]
    assert update["usage"]["pages_failed"] == 1  # type: ignore[index]
    assert update["usage"]["pages_indexed"] == 1  # type: ignore[index]
    assert update["rejected_urls"] == ["https://example.org/bad"]
    assert "page_failed" in update["warnings"]  # type: ignore[operator]
    assert state["accepted_urls"] == []


async def test_ingest_skips_a_url_already_accepted_in_this_run() -> None:
    state = merge_update(
        make_state(),
        {
            "candidate_urls": [
                {"url": "https://example.org/old", "title": "Old"},
                {"url": "https://example.org/new", "title": "New"},
            ],
            "accepted_urls": ["https://example.org/old"],
        },
    )
    ingestion = FakeIngestion()
    context = _context(ingestion=ingestion)

    await ingest_node(state, runtime_for(context))

    assert ingestion.calls == [(UUID(state["research_id"]), "https://example.org/new")]


async def test_retrieval_keeps_the_scope_the_better_score_and_a_short_excerpt() -> None:
    state = merge_update(make_state(), {"query_batches": [["alpha limits"]]})
    research_id = UUID(state["research_id"])
    chunk_id = uuid4()
    foreign = make_match(uuid4(), score=0.99, text="foreign")
    weaker = make_match(research_id, chunk_id=chunk_id, score=0.2, text="shorter")
    stronger = make_match(
        research_id,
        chunk_id=chunk_id,
        score=0.9,
        text="e" * 600,
        url="https://EXAMPLE.org/notes",
    )
    retrieval = FakeRetrieval([foreign, weaker, stronger])
    context = _context(retrieval=retrieval)

    update = await retrieve_node(state, runtime_for(context))

    assert retrieval.calls == [(research_id, "alpha limits", SEARCH_LIMIT)]
    evidence = update["evidence"]
    assert len(evidence) == 1  # type: ignore[arg-type]
    assert evidence[0]["research_id"] == state["research_id"]  # type: ignore[index]
    assert evidence[0]["score"] == 0.9  # type: ignore[index]
    assert evidence[0]["url"] == "https://example.org/notes"  # type: ignore[index]
    assert evidence[0]["excerpt"] == "e" * 500  # type: ignore[index]
    assert state["evidence"] == []


async def test_retrieval_failure_does_not_invent_a_report() -> None:
    state = merge_update(make_state(), {"query_batches": [["alpha limits"]]})
    context = _context(retrieval=FakeRetrieval(search_failed()))

    update = await retrieve_node(state, runtime_for(context))

    assert update["status"] == "failed"
    assert update["report"] is None
    assert update["error_code"] == "search_failed"
    assert state["status"] == "running"
    assert state["report"] is None
