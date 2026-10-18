"""Cursor codec for research history. No database."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from nexus.ai.errors import ResearchError
from nexus.research.history import decode_cursor, encode_cursor, sanitize_warnings

RUN_ID = UUID("00000000-0000-4000-8000-000000000001")
MOMENT = datetime(2026, 10, 18, 12, 30, tzinfo=UTC)


def test_cursor_round_trip() -> None:
    encoded = encode_cursor(MOMENT, RUN_ID)

    assert "=" not in encoded
    assert decode_cursor(encoded) == (MOMENT, RUN_ID)


def test_cursor_rejects_malformed_values() -> None:
    encoded = encode_cursor(MOMENT, RUN_ID)
    samples = [
        "",
        "not-a-cursor",
        encoded + "===",
        encode_cursor(MOMENT, RUN_ID)[:-1] + "*",
    ]

    for sample in samples:
        with pytest.raises(ResearchError) as exc_info:
            decode_cursor(sample)
        assert exc_info.value.code == "invalid_request"
        assert exc_info.value.status_code == 422


def test_cursor_rejects_extra_fields_and_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        encode_cursor(datetime(2026, 10, 18, 12, 30), RUN_ID)

    body = (
        '{"v":1,"created_at":"2026-10-18T12:30:00+00:00",'
        '"run_id":"00000000-0000-4000-8000-000000000001","extra":1}'
    )
    import base64

    padded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    with pytest.raises(ResearchError) as exc_info:
        decode_cursor(padded)
    assert exc_info.value.code == "invalid_request"


def test_warnings_keep_bounded_codes() -> None:
    warnings = ["budget_exhausted", "Budget exhausted", "budget_exhausted"]
    warnings.extend(f"code_{index}" for index in range(40))

    cleaned = sanitize_warnings(warnings)

    assert cleaned[0] == "budget_exhausted"
    assert "Budget exhausted" not in cleaned
    assert len(cleaned) == 32
    assert len(set(cleaned)) == 32
