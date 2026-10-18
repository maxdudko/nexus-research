"""SSE event contracts. These tests do not start a graph."""

from uuid import UUID

import pytest

from nexus.research.events import ResearchFailed, ResearchStarted
from nexus.research.runner import GraphProgress
from nexus.research.streaming import (
    SseWriter,
    encode_frame,
    encode_ping,
    encode_sse,
    progress_event,
)

RUN_ID = UUID("00000000-0000-4000-8000-000000000010")
RESEARCH_ID = UUID("00000000-0000-4000-8000-000000000011")


def test_frames_are_single_line_json() -> None:
    frame = encode_sse(
        "research.started",
        ResearchStarted(run_id=RUN_ID, research_id=RESEARCH_ID),
    )

    assert frame.startswith("event: research.started\n")
    assert frame.endswith("\n\n")
    assert "\r" not in frame
    data_line = frame.split("\n")[1]
    assert data_line.startswith("data: {")
    assert "\n" not in data_line


def test_encoder_rejects_a_raw_newline() -> None:
    with pytest.raises(ValueError, match="one line"):
        encode_frame("research.failed", '{"code":"bad"\n}')


def test_ping_is_a_comment() -> None:
    assert encode_ping() == ": ping\n\n"


def test_writer_sends_one_terminal_frame() -> None:
    writer = SseWriter()
    writer.event(
        "research.failed",
        ResearchFailed(run_id=RUN_ID, code="invalid_state"),
        terminal=True,
    )

    with pytest.raises(RuntimeError, match="terminal"):
        writer.ping()


def test_unknown_and_failed_nodes_are_omitted() -> None:
    unknown = GraphProgress(node="secret", iteration=0, pages_indexed=0, search_calls=0)
    failed = GraphProgress(node="fail", iteration=1, pages_indexed=0, search_calls=0)
    huge = GraphProgress(node="plan", iteration=0, pages_indexed=10001, search_calls=0)

    assert progress_event(unknown, RUN_ID) is None
    assert progress_event(failed, RUN_ID) is None
    assert progress_event(huge, RUN_ID) is None


def test_progress_uses_the_server_label() -> None:
    event = progress_event(
        GraphProgress(node="discover", iteration=1, pages_indexed=2, search_calls=3),
        RUN_ID,
    )

    assert event is not None
    assert event.stage == "discovery"
    assert event.message == "Searching for sources"
    assert event.counts.pages_indexed == 2
    assert "http" not in event.model_dump_json()
