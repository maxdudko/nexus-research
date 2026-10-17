"""Ingestion adapter. One page failure does not fail the run."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from nexus.ai.errors import ResearchError
from nexus.research.budgets import BudgetLimits, charge_pages, pending_urls
from nexus.research.nodes.common import stopped
from nexus.research.state import ResearchState, UsageSnapshot, copy_usage

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext


async def ingest_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Index new public URLs for this research scope, up to the page cap."""
    if stopped(state):
        return {}
    limits = runtime.context.limits
    urls = pending_urls(
        state["candidate_urls"],
        state["accepted_urls"],
        state["rejected_urls"],
        state["usage"],
        limits,
    )
    if not urls:
        return {}
    research_id = UUID(state["research_id"])
    accepted_ids = list(state["accepted_source_ids"])
    accepted_urls = list(state["accepted_urls"])
    rejected = list(state["rejected_urls"])
    usage: UsageSnapshot = _copy_usage(state["usage"])
    warnings = list(state["warnings"])
    for url in urls:
        try:
            result = await runtime.context.ingestion.ingest_web(research_id, url)
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
        if source_id not in accepted_ids:
            accepted_ids.append(source_id)
        if url not in accepted_urls:
            accepted_urls.append(url)
        usage = charge_pages(usage, limits, indexed=1)
    return {
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
