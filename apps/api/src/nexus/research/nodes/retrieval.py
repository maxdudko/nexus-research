"""Retrieval adapter. Passages stay short and inside the research scope."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING
from uuid import UUID

from nexus.ai.errors import ResearchError
from nexus.research.budgets import merge_passages
from nexus.research.nodes.common import failed, stopped
from nexus.research.state import EvidenceState, ResearchState
from nexus.retrieval.vector_store import SEARCH_LIMIT
from nexus.tools.web_search import normalize_url

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext
    from nexus.retrieval.vector_store import EvidenceMatch


async def retrieve_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Search the accepted index with this round's queries only."""
    if stopped(state):
        return {}
    batch = state["query_batches"][-1] if state["query_batches"] else []
    if not batch:
        return {}
    limits = runtime.context.limits
    research_id = UUID(state["research_id"])
    top_k = min(SEARCH_LIMIT, limits.max_passages)
    found: list[EvidenceState] = []
    for query in batch:
        try:
            matches = await runtime.context.retrieval.search(research_id, query, top_k)
        except asyncio.CancelledError:
            raise
        except ResearchError as exc:
            return failed(exc.code)
        except Exception:
            return failed("provider_unavailable")
        for match in matches:
            passage = _passage(match, research_id)
            if passage is not None:
                found.append(passage)
    return {
        "evidence": merge_passages(
            state["evidence"],
            found,
            limit=limits.max_passages,
            excerpt_chars=limits.excerpt_chars,
        )
    }


def _passage(match: EvidenceMatch, research_id: UUID) -> EvidenceState | None:
    if match.research_id != research_id:
        return None
    if isinstance(match.score, bool) or not isinstance(match.score, int | float):
        return None
    if not math.isfinite(float(match.score)):
        return None
    page = match.page_number
    if page is not None and page < 1:
        page = None
    url = match.url
    if url is not None:
        url = normalize_url(url)
    return {
        "chunk_id": str(match.chunk_id),
        "source_id": str(match.source_id),
        "research_id": str(research_id),
        "url": url,
        "page_number": page,
        "score": float(match.score),
        "excerpt": match.text,
    }
