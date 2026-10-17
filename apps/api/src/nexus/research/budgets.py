"""Deterministic budgets for the research graph.

Discovery spends up to three searches and four model calls inside one
invocation. A round is allowed only when that worst case still fits, including
a reserve for later evaluation and synthesis. The actual counts on
DiscoveryResult are what get recorded.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nexus.core.config import Settings
from nexus.research.discovery import MAX_LLM_CALLS, MAX_SEARCH_CALLS
from nexus.research.state import (
    MAX_STORED_EXCERPT,
    CandidateUrl,
    EvidenceState,
    UsageSnapshot,
)
from nexus.schemas.research import QUERY_MAX_LENGTH, ResearchPlan
from nexus.tools.web_search import normalize_url

PLANNER_LLM_CALLS = 1
EVALUATION_LLM_RESERVE = 2
SYNTHESIS_LLM_RESERVE = 2
_MODEL_RESERVE = EVALUATION_LLM_RESERVE + SYNTHESIS_LLM_RESERVE


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


def limits_from_settings(settings: Settings) -> BudgetLimits:
    """Fail closed when the configured caps cannot hold the nested worst case."""
    search_floor = settings.graph_max_iterations * MAX_SEARCH_CALLS
    llm_floor = (
        PLANNER_LLM_CALLS
        + settings.graph_max_iterations * MAX_LLM_CALLS
        + _MODEL_RESERVE
    )
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

    The model reserve stays unused until evaluation and synthesis run, including
    while those nodes are stubs.
    """
    if iteration >= limits.max_iterations:
        return False
    remaining_search = limits.max_search_calls - usage["search_calls"]
    if remaining_search < limits.discovery_search_ceiling:
        return False
    remaining_llm = limits.max_llm_calls - usage["llm_calls"] - _MODEL_RESERVE
    return remaining_llm >= limits.discovery_llm_ceiling


def charge_planner(usage: UsageSnapshot, limits: BudgetLimits) -> UsageSnapshot:
    """Count the one logical planner call."""
    return _charge_llm(usage, limits, PLANNER_LLM_CALLS)


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
    updated = _copy_usage(usage)
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
) -> list[str]:
    """Take unused queries in task order. One round cannot exceed Discovery."""
    bounded = min(limit, MAX_SEARCH_CALLS)
    if bounded <= 0:
        return []
    parsed = (
        plan if isinstance(plan, ResearchPlan) else ResearchPlan.model_validate(plan)
    )
    used = {query_key(query) for batch in used_batches for query in batch}
    chosen: list[str] = []
    seen: set[str] = set()
    for task in parsed.tasks:
        for query in task.search_queries:
            key = query_key(query)
            if not key or key in used or key in seen or len(key) > QUERY_MAX_LENGTH:
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
) -> list[str]:
    """The next Discovery batch, or nothing when the worst case no longer fits."""
    if plan is None or not can_discover(usage, limits, iteration):
        return []
    remaining_search = limits.max_search_calls - usage["search_calls"]
    cap = min(limits.discovery_search_ceiling, remaining_search)
    return select_queries(plan, used_batches, limit=cap)


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
    return ordered[:limit]


def _copy_usage(usage: UsageSnapshot) -> UsageSnapshot:
    copied: UsageSnapshot = {
        "search_calls": usage["search_calls"],
        "llm_calls": usage["llm_calls"],
        "pages_indexed": usage["pages_indexed"],
        "pages_failed": usage["pages_failed"],
    }
    return copied


def _count(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _charge_llm(
    usage: UsageSnapshot, limits: BudgetLimits, calls: int
) -> UsageSnapshot:
    calls = _count(calls, "llm calls")
    updated = _copy_usage(usage)
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
