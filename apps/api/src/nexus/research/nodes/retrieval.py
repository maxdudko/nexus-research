"""Retrieval adapter. Passages stay short and inside the research scope."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import UUID

from nexus.ai.errors import ResearchError
from nexus.research.budgets import merge_passages, work_seconds
from nexus.research.nodes.common import before_provider, failed
from nexus.research.state import EvidenceState, ResearchState
from nexus.retrieval.vector_store import SEARCH_LIMIT
from nexus.schemas.research import ResearchPlan
from nexus.tools.web_search import normalize_url

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from nexus.research.graph import GraphContext
    from nexus.retrieval.vector_store import EvidenceMatch


async def retrieve_node(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> dict[str, object]:
    """Balance planned questions and this round's targeted gap queries."""
    gate = await before_provider(state, runtime.context.session_active)
    if gate is not None:
        return gate
    batch = state["query_batches"][-1] if state["query_batches"] else []
    if state["plan"] is not None:
        plan = ResearchPlan.model_validate(state["plan"])
        batch = list(dict.fromkeys([*(t.question for t in plan.tasks), *batch]))
    if not batch:
        return {}
    limits = runtime.context.limits
    research_id = UUID(state["research_id"])
    top_k = min(SEARCH_LIMIT, limits.max_passages)
    metadata = {url: dict(info) for url, info in state["source_metadata"].items()}
    found: list[EvidenceState] = []
    groups: list[list[EvidenceState]] = []
    for query in batch:
        seconds = work_seconds(state, limits, runtime.context.clock())
        if seconds <= 0:
            break
        try:
            async with asyncio.timeout(seconds):
                matches = await runtime.context.retrieval.search(
                    research_id,
                    query,
                    top_k,
                    owner_id=runtime.context.owner_user_id,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            break
        except ResearchError as exc:
            return failed(exc.code)
        except Exception:
            return failed("provider_unavailable")
        group: list[EvidenceState] = []
        for match in matches:
            passage = _passage(match, research_id)
            if passage is not None:
                group.append(passage)
                key = passage["url"] or "source:" + passage["source_id"]
                previous = metadata.get(key) or next(
                    (
                        info
                        for info in metadata.values()
                        if info.get("source_id") == passage["source_id"]
                    ),
                    {},
                )
                metadata[key] = {
                    **previous,
                    "source_id": passage["source_id"],
                    "title": match.title[:200]
                    if match.title
                    else previous.get("title"),
                    "domain": urlsplit(passage["url"]).hostname
                    if passage["url"]
                    else None,
                    "retrieved_at": previous.get("retrieved_at")
                    or runtime.context.clock().isoformat(),
                }
        groups.append(group)
    seen: set[str] = set()
    # Alternate tasks; duplicate/reprinted passages occupy one evidence slot.
    for rank in range(top_k):
        for group in groups:
            if rank < len(group):
                item = group[rank]
                key = " ".join(item["excerpt"].split()).casefold()
                if key not in seen:
                    seen.add(key)
                    found.append(item)
    return {
        "source_metadata": metadata,
        "evidence": merge_passages(
            state["evidence"],
            found,
            limit=limits.max_passages,
            excerpt_chars=limits.excerpt_chars,
            balanced=True,
        ),
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
