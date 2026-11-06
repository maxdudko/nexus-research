"""Deterministic budgets for the research graph.

Production discovery executes at most three searches and one ranking call.
The standalone legacy discovery endpoint retains its four-call tool loop.
Each graph profile reserves calls for analysis, writing and support checks;
only reported actual counts (or conservative ceilings on failure) are charged.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from nexus.core.config import Settings
from nexus.research.discovery import MAX_LLM_CALLS, MAX_SEARCH_CALLS
from nexus.research.state import (
    MAX_STORED_EXCERPT,
    CandidateUrl,
    EvidenceState,
    ResearchState,
    UsageSnapshot,
    copy_usage,
)
from nexus.schemas.coverage import task_label
from nexus.schemas.research import QUERY_MAX_LENGTH, ResearchDepth, ResearchPlan
from nexus.tools.web_search import normalize_url

PLANNER_LLM_CALLS = 1
EVALUATION_LLM_RESERVE = 4
SYNTHESIS_LLM_RESERVE = 2
QUALITY_LLM_RESERVE = 4


@dataclass(frozen=True)
class BudgetLimits:
    """Caps for one run. Discovery's own ceilings are imported, not copied."""

    max_iterations: int
    max_search_calls: int
    max_llm_calls: int
    max_web_pages: int
    max_passages: int
    deadline_seconds: float
    excerpt_chars: int
    discovery_search_ceiling: int
    discovery_llm_ceiling: int
    depth: ResearchDepth = "standard"
    finish_reserve_seconds: float = 45

    def snapshot(self) -> dict[str, object]:
        return asdict(self)


def profile_limits(settings: Settings, depth: ResearchDepth) -> BudgetLimits:
    # Named profiles are server policy, independent of legacy graph overrides.
    del settings
    base = BudgetLimits(
        max_iterations=2,
        max_search_calls=6,
        max_llm_calls=20,
        max_web_pages=8,
        max_passages=24,
        deadline_seconds=180,
        excerpt_chars=2000,
        discovery_search_ceiling=3,
        discovery_llm_ceiling=1,
    )
    if depth == "deep":
        return replace(
            base,
            depth=depth,
            max_iterations=4,
            max_search_calls=12,
            max_llm_calls=40,
            max_web_pages=20,
            max_passages=50,
            deadline_seconds=600,
            excerpt_chars=2000,
            discovery_llm_ceiling=1,
            finish_reserve_seconds=150,
        )
    return replace(
        base,
        depth=depth,
        max_iterations=2,
        max_search_calls=6,
        max_llm_calls=20,
        max_web_pages=8,
        max_passages=24,
        deadline_seconds=180,
        excerpt_chars=2000,
        discovery_llm_ceiling=1,
        finish_reserve_seconds=60,
    )


def limits_from_settings(settings: Settings) -> BudgetLimits:
    """Fail closed when the configured caps cannot hold the nested worst case."""
    search_floor = settings.graph_max_iterations * MAX_SEARCH_CALLS
    llm_floor = PLANNER_LLM_CALLS + settings.graph_max_iterations * MAX_LLM_CALLS + 4
    if settings.graph_max_search_calls < search_floor:
        raise ValueError("graph_max_search_calls cannot cover nested discovery")
    if settings.graph_max_llm_calls < llm_floor:
        raise ValueError("graph_max_llm_calls cannot cover nested discovery")
    if settings.graph_excerpt_chars > MAX_STORED_EXCERPT:
        raise ValueError("graph_excerpt_chars exceeds the checkpoint cap")
    return BudgetLimits(
        max_iterations=settings.graph_max_iterations,
        max_search_calls=settings.graph_max_search_calls,
        max_llm_calls=settings.graph_max_llm_calls,
        max_web_pages=settings.graph_max_web_pages,
        max_passages=settings.graph_max_passages,
        deadline_seconds=settings.graph_deadline_seconds,
        excerpt_chars=settings.graph_excerpt_chars,
        discovery_search_ceiling=MAX_SEARCH_CALLS,
        discovery_llm_ceiling=MAX_LLM_CALLS,
    )


def can_discover(usage: UsageSnapshot, limits: BudgetLimits, iteration: int) -> bool:
    """True when another Discovery call cannot exceed the global caps.

    Evaluation and synthesis hold only the calls they have not spent yet.
    A spent call is already in ``llm_calls`` and is not reserved again.
    """
    if iteration >= limits.max_iterations:
        return False
    remaining_search = limits.max_search_calls - usage["search_calls"]
    if remaining_search < limits.discovery_search_ceiling:
        return False
    remaining_llm = limits.max_llm_calls - usage["llm_calls"] - _held_reserve(usage)
    return remaining_llm >= limits.discovery_llm_ceiling


def charge_planner(usage: UsageSnapshot, limits: BudgetLimits) -> UsageSnapshot:
    """Count the one logical planner call."""
    return _charge_llm(usage, limits, PLANNER_LLM_CALLS)


def charge_evaluation(
    usage: UsageSnapshot,
    limits: BudgetLimits,
    *,
    calls: int,
) -> UsageSnapshot:
    """Count logical evaluation calls. Two visits share this reserve."""
    calls = _count(calls, "evaluation calls")
    if usage["evaluation_calls"] + calls > EVALUATION_LLM_RESERVE:
        raise ValueError("evaluation budget exceeded")
    updated = _charge_llm(usage, limits, calls)
    updated["evaluation_calls"] += calls
    return updated


def charge_synthesis(
    usage: UsageSnapshot,
    limits: BudgetLimits,
    *,
    calls: int,
) -> UsageSnapshot:
    """Count logical synthesis calls, including one malformed retry."""
    calls = _count(calls, "synthesis calls")
    if usage["synthesis_calls"] + calls > SYNTHESIS_LLM_RESERVE:
        raise ValueError("synthesis budget exceeded")
    updated = _charge_llm(usage, limits, calls)
    updated["synthesis_calls"] += calls
    return updated


def evaluation_call_allowed(usage: UsageSnapshot, limits: BudgetLimits) -> bool:
    """True when this visit can spend one call and still leave synthesis funded."""
    if usage["evaluation_calls"] >= EVALUATION_LLM_RESERVE:
        return False
    synthesis_left = max(0, SYNTHESIS_LLM_RESERVE - usage["synthesis_calls"])
    remaining = limits.max_llm_calls - usage["llm_calls"] - synthesis_left
    return remaining >= 1


def synthesis_calls_allowed(usage: UsageSnapshot, limits: BudgetLimits) -> int:
    """How many synthesis attempts still fit in the reserve and the global cap."""
    reserve_left = max(0, SYNTHESIS_LLM_RESERVE - usage["synthesis_calls"])
    budget_left = max(0, limits.max_llm_calls - usage["llm_calls"] - 3)
    return min(reserve_left, budget_left)


def charge_discovery(
    usage: UsageSnapshot,
    limits: BudgetLimits,
    *,
    search_calls: int,
    llm_calls: int,
) -> UsageSnapshot:
    """Add the counts Discovery actually reported, not one per invocation."""
    search_calls = _count(search_calls, "discovery search count")
    llm_calls = _count(llm_calls, "discovery model count")
    if search_calls > limits.discovery_search_ceiling:
        raise ValueError("discovery search count exceeds its own cap")
    if llm_calls > limits.discovery_llm_ceiling:
        raise ValueError("discovery model count exceeds its own cap")
    updated = _charge_llm(usage, limits, llm_calls)
    updated["search_calls"] += search_calls
    if updated["search_calls"] > limits.max_search_calls:
        raise ValueError("search budget exceeded")
    return updated


def charge_pages(
    usage: UsageSnapshot,
    limits: BudgetLimits,
    *,
    indexed: int = 0,
    failed: int = 0,
) -> UsageSnapshot:
    """Count an ingestion attempt. Failed pages consume the page cap too."""
    indexed = _count(indexed, "indexed pages")
    failed = _count(failed, "failed pages")
    updated = copy_usage(usage)
    updated["pages_indexed"] += indexed
    updated["pages_failed"] += failed
    attempted = updated["pages_indexed"] + updated["pages_failed"]
    if attempted > limits.max_web_pages:
        raise ValueError("page budget exceeded")
    return updated


def pages_remaining(usage: UsageSnapshot, limits: BudgetLimits) -> int:
    """How many more public pages this run may fetch."""
    used = usage["pages_indexed"] + usage["pages_failed"]
    return max(0, limits.max_web_pages - used)


def query_key(query: str) -> str:
    """Identity for a search query. Case and surrounding space do not count."""
    return " ".join(query.split()).casefold()


def select_queries(
    plan: ResearchPlan | Mapping[str, object],
    used_batches: Sequence[Sequence[str]],
    *,
    limit: int,
    missing_task_ids: Sequence[str] | None = None,
    blocked_urls: Sequence[str] | None = None,
) -> list[str]:
    """Take unused queries in task order. One round cannot exceed Discovery.

    An empty missing-id list means every task is still open. A query that is
    already a fetched or rejected URL is not sent again.
    """
    bounded = min(limit, MAX_SEARCH_CALLS)
    if bounded <= 0:
        return []
    parsed = (
        plan if isinstance(plan, ResearchPlan) else ResearchPlan.model_validate(plan)
    )
    used = {query_key(query) for batch in used_batches for query in batch}
    blocked = set(blocked_urls or [])
    open_tasks = set(missing_task_ids) if missing_task_ids else None
    chosen: list[str] = []
    seen: set[str] = set()
    for query_index in range(3):
        for index, task in enumerate(parsed.tasks, start=1):
            if open_tasks is not None and task_label(index) not in open_tasks:
                continue
            if query_index >= len(task.search_queries):
                continue
            query = task.search_queries[query_index]
            key = query_key(query)
            if not key or key in used or key in seen or len(key) > QUERY_MAX_LENGTH:
                continue
            normalized = normalize_url(query)
            if normalized is not None and normalized in blocked:
                continue
            chosen.append(" ".join(query.split()))
            seen.add(key)
            if len(chosen) >= bounded:
                return chosen
    return chosen


def next_query_batch(
    plan: ResearchPlan | Mapping[str, object] | None,
    used_batches: Sequence[Sequence[str]],
    usage: UsageSnapshot,
    limits: BudgetLimits,
    iteration: int,
    *,
    missing_task_ids: Sequence[str] | None = None,
    blocked_urls: Sequence[str] | None = None,
) -> list[str]:
    """The next Discovery batch, or nothing when the worst case no longer fits."""
    if plan is None or not can_discover(usage, limits, iteration):
        return []
    remaining_search = limits.max_search_calls - usage["search_calls"]
    cap = min(limits.discovery_search_ceiling, remaining_search)
    return select_queries(
        plan,
        used_batches,
        limit=cap,
        missing_task_ids=missing_task_ids,
        blocked_urls=blocked_urls,
    )


def batch_for_state(state: ResearchState, limits: BudgetLimits) -> list[str]:
    """The batch the router and the discovery node both use."""
    analysis = state.get("analysis")
    if analysis and analysis.get("next_queries"):
        used = {query_key(q) for batch in state["query_batches"] for q in batch}
        from nexus.schemas.analysis import EvidenceAnalysis

        parsed_analysis = EvidenceAnalysis.model_validate(analysis)
        fresh: list[str] = []
        for query in parsed_analysis.next_queries:
            key = query_key(query)
            if key not in used:
                used.add(key)
                fresh.append(" ".join(query.split()))
        if fresh and can_discover(state["usage"], limits, state["iteration"]):
            return fresh[: limits.discovery_search_ceiling]
    coverage = state["coverage"]
    missing = None if coverage is None else coverage["missing_task_ids"]
    blocked = [*state["accepted_urls"], *state["rejected_urls"]]
    return next_query_batch(
        state["plan"],
        state["query_batches"],
        state["usage"],
        limits,
        state["iteration"],
        missing_task_ids=missing,
        blocked_urls=blocked,
    )


def is_new_batch(batch: Sequence[str], previous: Sequence[Sequence[str]]) -> bool:
    """False for an empty batch or one already sent, ignoring case."""
    if not batch:
        return False
    key = [query_key(query) for query in batch]
    return all([query_key(query) for query in item] != key for item in previous)


def discovery_question(question: str, queries: Sequence[str]) -> str:
    """Point Discovery at the selected queries. The model may still ignore them."""
    if not queries:
        return question
    lines = "\n".join(
        f"{index}. {query}" for index, query in enumerate(queries, start=1)
    )
    return (
        f"{question}\n\n"
        "Search only for these queries, one tool call each, in this order:\n"
        f"{lines}"
    )


def cap_excerpt(text: str, limit: int) -> str:
    """Keep a passage short enough for a checkpoint."""
    if limit < 1:
        raise ValueError("excerpt limit must be positive")
    return text[:limit]


def add_candidates(
    existing: Sequence[CandidateUrl],
    found: Sequence[tuple[str, str]],
) -> list[CandidateUrl]:
    """Append normalized URLs that are not already candidates. Input is unchanged."""
    seen = {item["url"] for item in existing}
    added: list[CandidateUrl] = [
        {"url": item["url"], "title": item["title"]} for item in existing
    ]
    for url, title in found:
        normalized = normalize_url(url)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        added.append({"url": normalized, "title": " ".join(title.split())[:200]})
    return added


def pending_urls(
    candidates: Sequence[CandidateUrl],
    accepted_urls: Sequence[str],
    rejected_urls: Sequence[str],
    usage: UsageSnapshot,
    limits: BudgetLimits,
) -> list[str]:
    """URLs still worth fetching, in candidate order, inside the page cap."""
    skip = set(accepted_urls) | set(rejected_urls)
    remaining = pages_remaining(usage, limits)
    chosen: list[str] = []
    for candidate in candidates:
        if remaining <= 0:
            break
        url = candidate["url"]
        if url in skip:
            continue
        chosen.append(url)
        remaining -= 1
    return chosen


def merge_passages(
    existing: Sequence[EvidenceState],
    found: Sequence[EvidenceState],
    *,
    limit: int,
    excerpt_chars: int,
    balanced: bool = False,
) -> list[EvidenceState]:
    """Keep the higher score per chunk and cap the stored set. Inputs stay put."""
    by_id: dict[str, EvidenceState] = {}
    for item in existing:
        by_id[item["chunk_id"]] = _copy_passage(item, excerpt_chars)
    for item in found:
        copied = _copy_passage(item, excerpt_chars)
        previous = by_id.get(copied["chunk_id"])
        if previous is None or copied["score"] > previous["score"]:
            by_id[copied["chunk_id"]] = copied
    ordered = sorted(
        by_id.values(),
        key=lambda item: (-item["score"], item["source_id"], item["chunk_id"]),
    )
    if balanced:
        ids = dict.fromkeys(item["chunk_id"] for item in [*found, *existing])
        selected: list[EvidenceState] = []
        texts: set[str] = set()
        for chunk_id in ids:
            item = by_id[chunk_id]
            key = " ".join(item["excerpt"].split()).casefold()
            if key not in texts:
                texts.add(key)
                selected.append(item)
        return selected[:limit]
    return ordered[:limit]


def _held_reserve(usage: UsageSnapshot) -> int:
    evaluation_left = max(0, EVALUATION_LLM_RESERVE - usage["evaluation_calls"])
    synthesis_left = max(0, SYNTHESIS_LLM_RESERVE - usage["synthesis_calls"])
    return evaluation_left + synthesis_left + QUALITY_LLM_RESERVE


def _count(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _charge_llm(
    usage: UsageSnapshot, limits: BudgetLimits, calls: int
) -> UsageSnapshot:
    calls = _count(calls, "llm calls")
    updated = copy_usage(usage)
    updated["llm_calls"] += calls
    if updated["llm_calls"] > limits.max_llm_calls:
        raise ValueError("llm budget exceeded")
    return updated


def _copy_passage(item: EvidenceState, excerpt_chars: int) -> EvidenceState:
    return {
        "chunk_id": item["chunk_id"],
        "source_id": item["source_id"],
        "research_id": item["research_id"],
        "url": item["url"],
        "page_number": item["page_number"],
        "score": float(item["score"]),
        "excerpt": cap_excerpt(item["excerpt"], excerpt_chars),
    }


def work_seconds(state: ResearchState, limits: BudgetLimits, now: datetime) -> float:
    """Time left for evidence collection; synthesis and verification own the reserve."""
    from datetime import datetime

    elapsed = (now - datetime.fromisoformat(state["started_at"])).total_seconds()
    return max(0, limits.deadline_seconds - limits.finish_reserve_seconds - elapsed)
