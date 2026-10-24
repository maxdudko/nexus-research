"""Window and duration rules that do not need a database."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from nexus.admin.diagnostics import (
    MAX_WINDOW,
    STALE_RUNNING_AFTER,
    decode_run_cursor,
    duration_seconds,
    encode_run_cursor,
    resolve_window,
)
from nexus.ai.errors import ResearchError

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
RUN = "11111111-1111-4111-8111-111111111111"


def test_the_default_window_is_seven_days_and_ninety_days_is_the_maximum() -> None:
    start, end = resolve_window(NOW, None, None)
    assert end == NOW
    assert start == NOW - timedelta(days=7)
    assert STALE_RUNNING_AFTER == timedelta(minutes=10)
    assert MAX_WINDOW == timedelta(days=90)
    opened, closed = resolve_window(NOW, NOW - MAX_WINDOW, NOW)
    assert closed - opened == MAX_WINDOW


def test_a_bad_window_names_the_field_that_failed() -> None:
    with pytest.raises(ResearchError) as window:
        resolve_window(NOW, NOW - timedelta(days=91), NOW)
    assert window.value.status_code == 422
    assert window.value.message == "window: invalid value"
    with pytest.raises(ResearchError) as empty:
        resolve_window(NOW, NOW, NOW)
    assert empty.value.message == "window: invalid value"
    with pytest.raises(ResearchError) as start:
        resolve_window(NOW, NOW.replace(tzinfo=None), NOW)
    assert start.value.message == "from: invalid value"
    with pytest.raises(ResearchError) as end:
        resolve_window(NOW, NOW - timedelta(days=1), None)
    assert end.value.message == "to: invalid value"


def test_duration_uses_both_clocks_and_millisecond_strings() -> None:
    started = NOW
    assert duration_seconds(started, started + timedelta(milliseconds=1500)) == "1.500"
    assert duration_seconds(started, started) == "0.000"
    assert duration_seconds(started, None) is None
    assert duration_seconds(None, started) is None
    assert duration_seconds(started, started - timedelta(seconds=1)) is None


def test_a_run_cursor_round_trips_and_rejects_extra_keys() -> None:
    token = encode_run_cursor(NOW, UUID(RUN))
    created_at, run_id = decode_run_cursor(token)
    assert created_at == NOW
    assert str(run_id) == RUN
    with pytest.raises(ResearchError) as caught:
        decode_run_cursor(token + "x")
    assert caught.value.message == "cursor: invalid value"
