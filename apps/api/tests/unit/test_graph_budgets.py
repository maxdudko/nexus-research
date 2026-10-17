"""Budget gates. Discovery's nested calls are counted, not the invocation."""

import pytest
from tests.conftest import make_settings

from nexus.research.budgets import (
    add_candidates,
    can_discover,
    charge_discovery,
    charge_evaluation,
    charge_pages,
    charge_planner,
    charge_synthesis,
    is_new_batch,
    limits_from_settings,
    next_query_batch,
    pending_urls,
    select_queries,
)
from nexus.research.state import empty_usage
from nexus.schemas.research import ResearchPlan, ResearchTask


def _plan() -> ResearchPlan:
    tasks = [
        ResearchTask(
            question=f"What does task {task_index} cover?",
            search_queries=[
                f"query {task_index}-{query_index}" for query_index in range(3)
            ],
        )
        for task_index in range(3)
    ]
    return ResearchPlan(objective="Cover nine planned queries.", tasks=tasks)


def test_default_caps_cover_two_discovery_rounds() -> None:
    limits = limits_from_settings(make_settings())

    assert limits.max_iterations == 2
    assert limits.max_search_calls == 6
    assert limits.max_llm_calls == 13
    assert limits.max_web_pages == 5
    assert limits.max_passages == 12
    assert limits.deadline_seconds == 180
    assert limits.excerpt_chars == 500
    assert limits.discovery_search_ceiling == 3
    assert limits.discovery_llm_ceiling == 4


@pytest.mark.parametrize(
    "override",
    [{"graph_max_search_calls": 5}, {"graph_max_llm_calls": 12}],
)
def test_caps_below_the_nested_worst_case_fail(override: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        limits_from_settings(make_settings(**override))


def test_discovery_is_refused_when_the_worst_case_does_not_fit() -> None:
    limits = limits_from_settings(make_settings())
    few_searches = empty_usage()
    few_searches["search_calls"] = 4
    few_searches["llm_calls"] = 1
    few_models = empty_usage()
    few_models["llm_calls"] = 6

    assert not can_discover(few_searches, limits, 0)
    assert not can_discover(few_models, limits, 0)


def test_two_iterations_record_actual_nested_counts() -> None:
    limits = limits_from_settings(make_settings())
    usage = empty_usage()
    usage = charge_planner(usage, limits)
    usage = charge_discovery(usage, limits, search_calls=1, llm_calls=2)

    assert usage["search_calls"] == 1
    assert usage["llm_calls"] == 3
    assert can_discover(usage, limits, 1)

    usage = charge_discovery(usage, limits, search_calls=3, llm_calls=4)

    assert usage["search_calls"] == 4
    assert usage["llm_calls"] == 7
    assert not can_discover(usage, limits, 2)


def test_charging_does_not_mutate_the_previous_usage() -> None:
    limits = limits_from_settings(make_settings())
    usage = empty_usage()

    charged = charge_planner(usage, limits)

    assert usage["llm_calls"] == 0
    assert charged["llm_calls"] == 1
    assert charged is not usage


def test_nine_queries_cannot_bypass_the_search_cap() -> None:
    limits = limits_from_settings(make_settings())
    plan = _plan()
    usage = charge_planner(empty_usage(), limits)
    first = next_query_batch(plan, [], usage, limits, 0)
    usage = charge_discovery(usage, limits, search_calls=3, llm_calls=4)
    second = next_query_batch(plan, [first], usage, limits, 1)
    usage = charge_discovery(usage, limits, search_calls=3, llm_calls=4)
    third = next_query_batch(plan, [first, second], usage, limits, 2)

    assert first == ["query 0-0", "query 0-1", "query 0-2"]
    assert select_queries(plan, [], limit=9) == first
    assert second == ["query 1-0", "query 1-1", "query 1-2"]
    assert set(first).isdisjoint(second)
    assert third == []
    assert len(first) + len(second) == 6
    assert not can_discover(usage, limits, 2)


def test_query_identity_ignores_case_and_space() -> None:
    plan = ResearchPlan(
        objective="Compare two tasks.",
        tasks=[
            ResearchTask(
                question="What is the first task?",
                search_queries=["Alpha Query"],
            ),
            ResearchTask(
                question="What is the second task?",
                search_queries=["alpha   query", "Beta"],
            ),
        ],
    )

    assert select_queries(plan, [], limit=3) == ["Alpha Query", "Beta"]
    assert is_new_batch(["Alpha"], [["alpha"]]) is False


def test_candidate_urls_are_normalized_without_mutating_input() -> None:
    existing = [{"url": "https://example.org/a", "title": "A"}]
    added = add_candidates(
        existing,
        [
            ("https://EXAMPLE.org/a", "Ignored"),
            ("https://example.org/b", "B"),
        ],
    )

    assert existing == [{"url": "https://example.org/a", "title": "A"}]
    assert [item["url"] for item in added] == [
        "https://example.org/a",
        "https://example.org/b",
    ]


def test_spent_evaluation_is_not_reserved_again() -> None:
    limits = limits_from_settings(make_settings())
    usage = charge_planner(empty_usage(), limits)
    usage = charge_discovery(usage, limits, search_calls=3, llm_calls=4)
    usage = charge_evaluation(usage, limits, calls=1)

    assert usage["llm_calls"] == 6
    assert usage["evaluation_calls"] == 1
    assert can_discover(usage, limits, 1)

    with pytest.raises(ValueError):
        charge_evaluation(usage, limits, calls=2)
    with pytest.raises(ValueError):
        charge_synthesis(usage, limits, calls=3)


def test_missing_tasks_do_not_reuse_another_task_query() -> None:
    plan = _plan()
    first = select_queries(plan, [], limit=3)

    assert select_queries(
        plan,
        [first],
        limit=3,
        missing_task_ids=["T2"],
    ) == ["query 1-0", "query 1-1", "query 1-2"]
    assert select_queries(plan, [first], limit=3, missing_task_ids=["T1"]) == []


def test_a_query_that_is_a_rejected_url_is_skipped() -> None:
    plan = ResearchPlan(
        objective="Find the practical limits.",
        tasks=[
            ResearchTask(
                question="Which page was already fetched?",
                search_queries=["https://example.org/notes", "fresh query"],
            )
        ],
    )

    assert select_queries(
        plan,
        [],
        limit=3,
        blocked_urls=["https://example.org/notes"],
    ) == ["fresh query"]


def test_pending_urls_skip_accepted_pages_and_stop_at_the_cap() -> None:
    limits = limits_from_settings(make_settings())
    usage = charge_pages(empty_usage(), limits, indexed=4)
    candidates = [
        {"url": "https://example.org/a", "title": "A"},
        {"url": "https://example.org/b", "title": "B"},
        {"url": "https://example.org/c", "title": "C"},
    ]

    assert pending_urls(
        candidates,
        ["https://example.org/a"],
        [],
        usage,
        limits,
    ) == ["https://example.org/b"]
