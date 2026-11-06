"""Ingestion adapter. One page failure does not fail the run."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import UUID

from nexus.ai.errors import ResearchError
from nexus.research.budgets import (
    BudgetLimits,
    charge_pages,
    pages_remaining,
    pending_urls,
    work_seconds,
)
from nexus.research.nodes.common import before_provider
from nexus.research.state import ResearchState, UsageSnapshot, copy_usage

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext


async def ingest_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Index new public URLs for this research scope, up to the page cap."""
    gate = await before_provider(state, runtime.context.session_active)
    if gate is not None:
        return gate
    limits = runtime.context.limits
    all_candidates = state["candidate_urls"]
    urls = pending_urls(
        all_candidates,
        state["accepted_urls"],
        state["rejected_urls"],
        state["usage"],
        limits,
    )
    if not urls:
        return {}
    # Leave page capacity for evidence gaps discovered in subsequent rounds.
    remaining_rounds = max(1, limits.max_iterations - state["iteration"] + 1)
    capacity = (
        limits.max_web_pages
        - state["usage"]["pages_indexed"]
        - state["usage"]["pages_failed"]
    )
    per_round = min(len(urls), math.ceil(capacity / remaining_rounds))
    skip = set(state["accepted_urls"]) | set(state["rejected_urls"])
    urls = [item["url"] for item in all_candidates if item["url"] not in skip]
    metadata = state["source_metadata"]
    latest_queries = (
        set(state["query_batches"][-1]) if state["query_batches"] else set()
    )
    chosen: list[str] = []
    domains: dict[str, int] = {}
    queries: dict[str, int] = {}
    while urls and len(chosen) < capacity:

        def priority(url: str) -> tuple[int, int, int, float]:
            info = metadata.get(url, {})
            score = info.get("relevance_score", 0)
            return (
                int(bool(latest_queries) and info.get("query") not in latest_queries),
                queries.get(str(info.get("query", "")), 0),
                domains.get(urlsplit(url).hostname or "", 0),
                -float(score) if isinstance(score, (int, float)) else 0,
            )

        url = min(urls, key=priority)
        urls.remove(url)
        chosen.append(url)
        domain = urlsplit(url).hostname or ""
        query = str(metadata.get(url, {}).get("query", ""))
        domains[domain] = domains.get(domain, 0) + 1
        queries[query] = queries.get(query, 0) + 1
    urls = chosen
    research_id = UUID(state["research_id"])
    accepted_ids = list(state["accepted_source_ids"])
    accepted_urls = list(state["accepted_urls"])
    rejected = list(state["rejected_urls"])
    usage: UsageSnapshot = _copy_usage(state["usage"])
    warnings = list(state["warnings"])
    source_metadata = {
        url: dict(info) for url, info in state["source_metadata"].items()
    }
    indexed_this_round = 0
    for url in urls:
        # Failed candidates consume the global attempt cap, but should not
        # prevent other available pages from filling this round's evidence set.
        if indexed_this_round >= per_round or pages_remaining(usage, limits) <= 0:
            break
        seconds = work_seconds(state, limits, runtime.context.clock())
        if seconds <= 0:
            break
        try:
            async with asyncio.timeout(seconds):
                result = await runtime.context.ingestion.ingest_web(
                    research_id,
                    url,
                    owner_id=runtime.context.owner_user_id,
                )
        except asyncio.CancelledError:
            raise
        except ResearchError:
            usage, rejected, warnings = _reject(url, usage, rejected, warnings, limits)
            continue
        except Exception:
            usage, rejected, warnings = _reject(url, usage, rejected, warnings, limits)
            continue
        if result.research_id != research_id:
            usage, rejected, warnings = _reject(url, usage, rejected, warnings, limits)
            continue
        source_id = str(result.source_id)
        if url in source_metadata:
            source_metadata[url]["source_id"] = source_id
            source_metadata[url]["retrieved_at"] = runtime.context.clock().isoformat()
        if source_id not in accepted_ids:
            accepted_ids.append(source_id)
        if url not in accepted_urls:
            accepted_urls.append(url)
        usage = charge_pages(usage, limits, indexed=1)
        indexed_this_round += 1
    return {
        "source_metadata": source_metadata,
        "accepted_source_ids": accepted_ids,
        "accepted_urls": accepted_urls,
        "rejected_urls": rejected,
        "usage": usage,
        "warnings": warnings,
    }


def _copy_usage(usage: UsageSnapshot) -> UsageSnapshot:
    return copy_usage(usage)


def _reject(
    url: str,
    usage: UsageSnapshot,
    rejected: list[str],
    warnings: list[str],
    limits: BudgetLimits,
) -> tuple[UsageSnapshot, list[str], list[str]]:
    updated = charge_pages(usage, limits, failed=1)
    if url not in rejected:
        rejected.append(url)
    if "page_failed" not in warnings:
        warnings.append("page_failed")
    return updated, rejected, warnings
