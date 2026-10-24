"""Windowed research diagnostics. Queries do not read question or report text."""

import base64
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import ColumnElement, Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.admin.schemas import EvaluationListItem
from nexus.ai.errors import invalid_request
from nexus.models.run import RUN_STATUSES, ResearchRun

STALE_RUNNING_AFTER = timedelta(minutes=10)
DEFAULT_WINDOW = timedelta(days=7)
MAX_WINDOW = timedelta(days=90)
_MILLI = Decimal("0.001")
_CURSOR_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
_CURSOR_KEYS = frozenset({"v", "created_at", "run_id"})
_MAX_CURSOR = 512
StatusName = Literal[
    "running",
    "completed",
    "partial",
    "insufficient_evidence",
    "failed",
    "interrupted",
]
_SUCCESS = frozenset({"completed", "partial", "insufficient_evidence"})
_FAILURE = frozenset({"failed", "interrupted"})


class DiagnosticsUnreadable(Exception):
    """A stored row failed the allowlist. The payload is not included."""


class StatusCounts(BaseModel):
    """Every stored status, plus the recorded groups."""

    model_config = ConfigDict(extra="forbid")

    running: int = Field(ge=0)
    completed: int = Field(ge=0)
    partial: int = Field(ge=0)
    insufficient_evidence: int = Field(ge=0)
    failed: int = Field(ge=0)
    interrupted: int = Field(ge=0)
    recorded_success: int = Field(ge=0)
    recorded_failure: int = Field(ge=0)
    recorded_running: int = Field(ge=0)


class ErrorCount(BaseModel):
    """How many runs stored one sanitized error code."""

    model_config = ConfigDict(extra="forbid")

    error_code: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    count: int = Field(ge=0)


class DurationSummary(BaseModel):
    """Known elapsed time. Missing clocks stay null, not zero."""

    model_config = ConfigDict(extra="forbid")

    known_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    total_seconds: str | None
    max_seconds: str | None


class DiagnosticsSummary(BaseModel):
    """One time window of research status, errors, and duration."""

    model_config = ConfigDict(extra="forbid")

    window_start: datetime
    window_end: datetime
    statuses: StatusCounts
    stale_running: int = Field(ge=0)
    running_without_start: int = Field(ge=0)
    errors: list[ErrorCount]
    duration: DurationSummary


class DiagnosticRun(BaseModel):
    """One technical row. The question and owner are not fields."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: StatusName
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration: str | None


class DiagnosticRunPage(BaseModel):
    """One keyset page of technical runs."""

    model_config = ConfigDict(extra="forbid")

    items: list[DiagnosticRun]
    next_cursor: str | None


class OverviewResponse(BaseModel):
    """The default window plus the newest evaluation card."""

    model_config = ConfigDict(extra="forbid")

    diagnostics: DiagnosticsSummary
    latest_evaluation: EvaluationListItem | None


def resolve_window(
    now: datetime,
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime, datetime]:
    """Return an aware window. The default is the last seven days."""
    if start is None and end is None:
        moment = _utc(now)
        return moment - DEFAULT_WINDOW, moment
    if start is None or start.tzinfo is None:
        raise invalid_request("from")
    if end is None or end.tzinfo is None:
        raise invalid_request("to")
    opened = start.astimezone(UTC)
    closed = end.astimezone(UTC)
    if closed <= opened or closed - opened > MAX_WINDOW:
        raise invalid_request("window")
    return opened, closed


def duration_seconds(
    started_at: datetime | None,
    finished_at: datetime | None,
) -> str | None:
    """Elapsed seconds, or null when either clock is missing or reversed."""
    if started_at is None or finished_at is None or finished_at < started_at:
        return None
    delta = finished_at - started_at
    seconds = Decimal(delta.days * 86_400 + delta.seconds) + (
        Decimal(delta.microseconds) / Decimal(1_000_000)
    )
    return format(seconds.quantize(_MILLI), "f")


async def diagnostics_summary(
    factory: async_sessionmaker[AsyncSession],
    now: datetime,
    start: datetime | None,
    end: datetime | None,
) -> DiagnosticsSummary:
    """Count statuses inside the window. Question text is not selected."""
    opened, closed = resolve_window(now, start, end)
    async with factory() as session:
        status_rows = (
            await session.execute(
                select(ResearchRun.status, func.count())
                .where(_window(opened, closed))
                .group_by(ResearchRun.status)
            )
        ).all()
        error_rows = (
            await session.execute(
                select(ResearchRun.error_code, func.count())
                .where(_window(opened, closed), ResearchRun.error_code.is_not(None))
                .group_by(ResearchRun.error_code)
                .order_by(ResearchRun.error_code)
            )
        ).all()
        stale = await session.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .where(
                _window(opened, closed),
                ResearchRun.status == "running",
                ResearchRun.started_at.is_not(None),
                ResearchRun.started_at < now - STALE_RUNNING_AFTER,
            )
        )
        missing_start = await session.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .where(
                _window(opened, closed),
                ResearchRun.status == "running",
                ResearchRun.started_at.is_(None),
            )
        )
        clocks = (
            await session.execute(
                select(ResearchRun.started_at, ResearchRun.finished_at).where(
                    _window(opened, closed)
                )
            )
        ).all()
    try:
        return DiagnosticsSummary(
            window_start=opened,
            window_end=closed,
            statuses=_statuses(status_rows),
            stale_running=int(stale or 0),
            running_without_start=int(missing_start or 0),
            errors=_errors(error_rows),
            duration=_duration(clocks),
        )
    except ValidationError:
        raise DiagnosticsUnreadable from None


async def list_diagnostic_runs(
    factory: async_sessionmaker[AsyncSession],
    now: datetime,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    cursor: str | None,
) -> DiagnosticRunPage:
    """Return one technical page. The sort is created time, then run id."""
    if not 1 <= limit <= 50:
        raise invalid_request("limit")
    opened, closed = resolve_window(now, start, end)
    bound = decode_run_cursor(cursor) if cursor else None
    async with factory() as session:
        statement = (
            select(
                ResearchRun.run_id,
                ResearchRun.status,
                ResearchRun.error_code,
                ResearchRun.created_at,
                ResearchRun.started_at,
                ResearchRun.finished_at,
            )
            .where(_window(opened, closed))
            .order_by(ResearchRun.created_at.desc(), ResearchRun.run_id.desc())
        )
        if bound is not None:
            created_at, run_id = bound
            statement = statement.where(
                (ResearchRun.created_at < created_at)
                | (
                    (ResearchRun.created_at == created_at)
                    & (ResearchRun.run_id < run_id)
                )
            )
        rows = (await session.execute(statement.limit(limit + 1))).all()
    visible = rows[:limit]
    try:
        items = [
            DiagnosticRun(
                run_id=row.run_id,
                status=row.status,
                error_code=row.error_code,
                created_at=row.created_at,
                started_at=row.started_at,
                finished_at=row.finished_at,
                duration=duration_seconds(row.started_at, row.finished_at),
            )
            for row in visible
        ]
    except ValidationError:
        raise DiagnosticsUnreadable from None
    next_cursor = None
    if len(rows) > limit and visible:
        last = visible[-1]
        next_cursor = encode_run_cursor(last.created_at, last.run_id)
    return DiagnosticRunPage(items=items, next_cursor=next_cursor)


def encode_run_cursor(created_at: datetime, run_id: UUID) -> str:
    """Encode a diagnostics keyset. The value is opaque to clients."""
    body = json.dumps(
        {
            "v": 1,
            "created_at": _utc(created_at).isoformat(),
            "run_id": str(run_id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def decode_run_cursor(value: str) -> tuple[datetime, UUID]:
    """Reject a cursor that is not a versioned timestamp and run id."""
    try:
        if (
            not value
            or len(value) > _MAX_CURSOR
            or any(char not in _CURSOR_ALPHABET for char in value)
        ):
            raise ValueError
        padded = value + ("=" * (-len(value) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(payload, dict) or set(payload) != _CURSOR_KEYS:
            raise ValueError
        if payload.get("v") != 1 or isinstance(payload.get("v"), bool):
            raise ValueError
        created_text = payload["created_at"]
        run_text = payload["run_id"]
        if not isinstance(created_text, str) or not isinstance(run_text, str):
            raise ValueError
        created_at = datetime.fromisoformat(created_text)
        if created_at.tzinfo is None:
            raise ValueError
        run_id = UUID(run_text)
        if str(run_id) != run_text:
            raise ValueError
    except ValueError, json.JSONDecodeError, UnicodeError:
        raise invalid_request("cursor") from None
    return created_at, run_id


def _window(start: datetime, end: datetime) -> ColumnElement[bool]:
    return (ResearchRun.created_at >= start) & (ResearchRun.created_at < end)


def _statuses(rows: Sequence[Row[tuple[str, int]]]) -> StatusCounts:
    counts = {status: 0 for status in RUN_STATUSES}
    for status, count in rows:
        if status not in counts:
            raise DiagnosticsUnreadable
        counts[status] = count
    success = sum(counts[status] for status in _SUCCESS)
    failure = sum(counts[status] for status in _FAILURE)
    return StatusCounts(
        running=counts["running"],
        completed=counts["completed"],
        partial=counts["partial"],
        insufficient_evidence=counts["insufficient_evidence"],
        failed=counts["failed"],
        interrupted=counts["interrupted"],
        recorded_success=success,
        recorded_failure=failure,
        recorded_running=counts["running"],
    )


def _errors(rows: Sequence[Row[tuple[str | None, int]]]) -> list[ErrorCount]:
    errors: list[ErrorCount] = []
    for code, count in rows:
        if not isinstance(code, str):
            raise DiagnosticsUnreadable
        errors.append(ErrorCount(error_code=code, count=count))
    return errors


def _duration(
    rows: Sequence[Row[tuple[datetime | None, datetime | None]]],
) -> DurationSummary:
    known: list[Decimal] = []
    unknown = 0
    for started_at, finished_at in rows:
        text = duration_seconds(started_at, finished_at)
        if text is None:
            unknown += 1
        else:
            known.append(Decimal(text))
    if not known:
        return DurationSummary(
            known_count=0,
            unknown_count=unknown,
            total_seconds=None,
            max_seconds=None,
        )
    return DurationSummary(
        known_count=len(known),
        unknown_count=unknown,
        total_seconds=format(sum(known, Decimal(0)).quantize(_MILLI), "f"),
        max_seconds=format(max(known).quantize(_MILLI), "f"),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError
    return value.astimezone(UTC)
