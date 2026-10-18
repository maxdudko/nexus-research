"""SSE framing and the allowlisted node-to-stage map."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from pydantic import BaseModel, ValidationError

from nexus.research.events import (
    ProgressCounts,
    ResearchProgress,
    StageName,
)
from nexus.research.runner import GraphProgress

_STAGES: dict[str, StageName] = {
    "plan": "planning",
    "discover": "discovery",
    "ingest": "ingestion",
    "retrieve": "retrieval",
    "evaluate": "evaluation",
    "synthesize": "synthesis",
}
_MESSAGES: dict[StageName, str] = {
    "planning": "Planning the research",
    "discovery": "Searching for sources",
    "ingestion": "Indexing sources",
    "retrieval": "Selecting evidence",
    "evaluation": "Checking coverage",
    "synthesis": "Writing the report",
}


class Heartbeat:
    """A liveness comment. It is not a progress event."""


HEARTBEAT = Heartbeat()


def progress_event(update: GraphProgress, run_id: UUID) -> ResearchProgress | None:
    """Map a known node. Unknown nodes and out-of-range counts are omitted."""
    stage = _STAGES.get(update.node)
    if stage is None:
        return None
    try:
        return ResearchProgress(
            run_id=run_id,
            stage=stage,
            iteration=update.iteration,
            message=_MESSAGES[stage],
            counts=ProgressCounts(
                pages_indexed=update.pages_indexed,
                search_calls=update.search_calls,
            ),
        )
    except ValidationError:
        return None


def encode_sse(event: str, payload: BaseModel) -> str:
    """Encode one SSE frame. The JSON data line cannot contain a raw newline."""
    return encode_frame(event, payload.model_dump_json())


def encode_frame(event: str, data: str) -> str:
    """Encode one event whose data is already a single JSON line."""
    if "\n" in data or "\r" in data:
        raise ValueError("sse data must be one line")
    return f"event: {event}\ndata: {data}\n\n"


def encode_ping() -> str:
    """A comment heartbeat. Proxies can treat it as activity."""
    return ": ping\n\n"


class SseWriter:
    """Allow progress and pings, then exactly one terminal frame."""

    def __init__(self) -> None:
        self.terminal = False

    def event(self, name: str, payload: BaseModel, *, terminal: bool) -> str:
        if self.terminal:
            raise RuntimeError("terminal event already sent")
        frame = encode_sse(name, payload)
        if terminal:
            self.terminal = True
        return frame

    def ping(self) -> str:
        if self.terminal:
            raise RuntimeError("terminal event already sent")
        return encode_ping()


async def with_heartbeat(
    source: AsyncIterator[object],
    interval: float,
) -> AsyncIterator[object]:
    """Yield source items, and a heartbeat when the next item is slow.

    Waiting does not cancel the source. Closing this generator cancels the
    pending read, which cancels the graph task underneath it.
    """
    iterator = aiter(source)
    pending: asyncio.Task[object] | None = None
    try:
        pending = asyncio.ensure_future(anext(iterator))
        while True:
            done, _pending = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield HEARTBEAT
                continue
            try:
                item = pending.result()
            except StopAsyncIteration:
                return
            yield item
            pending = asyncio.ensure_future(anext(iterator))
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError, Exception:
                pass
