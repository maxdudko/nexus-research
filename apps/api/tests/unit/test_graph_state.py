"""Checkpoint state stays JSON and cancellation cannot complete a run."""

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from nexus.research.budgets import cap_excerpt, merge_passages
from nexus.research.state import (
    GraphStateError,
    ResearchState,
    apply_update,
    initial_state,
    mark_interrupted,
    merge_update,
    validate_state,
)
from nexus.schemas.report import EvidenceRef, ReportSection, ResearchReport

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _state() -> ResearchState:
    return initial_state(
        research_id=uuid4(),
        run_id=uuid4(),
        question="How do bounded research graphs stop?",
        started_at=NOW,
    )


def _report(state: ResearchState, source_id: str, chunk_id: str) -> ResearchReport:
    return ResearchReport(
        title="Bounded graphs",
        summary="Retrieved evidence is listed in the sources.",
        sections=[
            ReportSection(
                heading="Evidence",
                content="See cited sources.",
                citations=["E1"],
            )
        ],
        limitations=[],
        status="completed",
        sources=[
            EvidenceRef(
                evidence_id="E1",
                source_id=source_id,
                chunk_id=chunk_id,
                research_id=state["research_id"],
                url="https://example.org/notes",
                page_number=None,
            )
        ],
    )


def test_state_round_trips_through_json() -> None:
    state = _state()
    source_id = str(uuid4())
    chunk_id = str(uuid4())
    finished = merge_update(
        state,
        {
            "status": "completed",
            "report": _report(state, source_id, chunk_id).model_dump(mode="json"),
            "evidence": [
                {
                    "chunk_id": chunk_id,
                    "source_id": source_id,
                    "research_id": state["research_id"],
                    "url": "https://example.org/notes",
                    "page_number": None,
                    "score": 0.5,
                    "excerpt": "A short excerpt.",
                }
            ],
        },
    )

    encoded = json.dumps(finished)
    loaded = validate_state(json.loads(encoded))

    assert loaded["research_id"] == state["research_id"]
    assert isinstance(loaded["research_id"], str)
    assert loaded["run_id"] == state["run_id"]
    assert loaded["report"]["status"] == "completed"  # type: ignore[index]
    assert "vector" not in encoded
    assert "html" not in encoded


def test_excerpt_is_capped_and_a_document_is_rejected() -> None:
    state = _state()
    source_id = str(uuid4())
    chunk_id = str(uuid4())
    original = "a" * 600
    passage = {
        "chunk_id": chunk_id,
        "source_id": source_id,
        "research_id": state["research_id"],
        "url": None,
        "page_number": 2,
        "score": 0.4,
        "excerpt": original,
    }

    assert cap_excerpt(original, 500) == "a" * 500
    merged = merge_passages([], [passage], limit=12, excerpt_chars=500)  # type: ignore[list-item]
    assert merged[0]["excerpt"] == "a" * 500
    assert passage["excerpt"] == original

    passage["excerpt"] = "b" * 2001
    with pytest.raises(GraphStateError):
        merge_update(state, {"evidence": [passage]})


def test_merge_update_leaves_input_lists_unchanged() -> None:
    state = _state()
    warnings = state["warnings"]

    updated = merge_update(state, {"warnings": ["budget_exhausted"]})

    assert warnings == []
    assert state["warnings"] is warnings
    assert updated["warnings"] == ["budget_exhausted"]
    assert updated["warnings"] is not warnings


def test_interrupted_state_is_a_copy_without_a_report() -> None:
    state = _state()

    updated = mark_interrupted(state)

    assert state["status"] == "running"
    assert state["report"] is None
    assert updated["status"] == "interrupted"
    assert updated["report"] is None
    assert updated["error_code"] == "interrupted"


async def test_cancelled_update_does_not_complete() -> None:
    state = _state()
    warnings = state["warnings"]

    async def work() -> Mapping[str, object]:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await apply_update(state, work)

    assert state["status"] == "running"
    assert state["report"] is None
    assert state["warnings"] is warnings


def test_non_json_and_negative_usage_are_rejected() -> None:
    state = _state()

    with pytest.raises(GraphStateError):
        merge_update(state, {"research_id": uuid4()})

    raw = json.loads(json.dumps(state))
    raw["usage"]["search_calls"] = -1
    with pytest.raises(GraphStateError):
        validate_state(raw)

    del raw["run_id"]
    raw["usage"]["search_calls"] = 0
    with pytest.raises(GraphStateError):
        validate_state(raw)


def test_unknown_citation_is_rejected() -> None:
    state = _state()
    source_id = str(uuid4())
    chunk_id = str(uuid4())

    with pytest.raises(ValidationError):
        ResearchReport(
            title="Bounded graphs",
            summary="Retrieved evidence is listed in the sources.",
            sections=[
                ReportSection(
                    heading="Evidence",
                    content="See cited sources.",
                    citations=["E9"],
                )
            ],
            limitations=[],
            status="completed",
            sources=[
                EvidenceRef(
                    evidence_id="E1",
                    source_id=source_id,
                    chunk_id=chunk_id,
                    research_id=state["research_id"],
                    url="https://example.org/notes",
                    page_number=None,
                )
            ],
        )


def test_naive_start_time_is_rejected() -> None:
    with pytest.raises(GraphStateError):
        initial_state(
            research_id=uuid4(),
            run_id=uuid4(),
            question="How do bounded research graphs stop?",
            started_at=NOW.replace(tzinfo=None),
        )


def test_schema_version_is_current() -> None:
    raw = json.loads(json.dumps(_state()))
    raw["schema_version"] = 2
    raw["started_at"] = (NOW - timedelta(seconds=1)).isoformat()

    with pytest.raises(GraphStateError):
        validate_state(raw)
