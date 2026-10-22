"""Short transactions for research history. A session never spans a graph run."""

import base64
import json
import re
from datetime import datetime
from typing import cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.ai.errors import invalid_request, invalid_state, not_found
from nexus.models.run import SUCCESS_STATUSES, ResearchReportRecord, ResearchRun
from nexus.models.scope import ResearchScope
from nexus.schemas.history import (
    RunDetail,
    RunDetailResponse,
    RunListResponse,
    RunStatus,
    RunSummary,
)
from nexus.schemas.report import GraphUsage, ResearchReport

_WARNING = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
_MAX_WARNINGS = 32
_LIST_LIMIT = 50
_CURSOR_KEYS = frozenset({"v", "created_at", "run_id"})
_FAILED_CODES = frozenset(
    {
        "model_refused",
        "provider_not_configured",
        "provider_timeout",
        "provider_rate_limited",
        "provider_unavailable",
        "invalid_model_output",
        "invalid_state",
        "search_failed",
        "checkpoint_unavailable",
        "history_unavailable",
    }
)
_INTERRUPT_CODES = frozenset({"interrupted", "deadline_exceeded"})
_RUN_STATUSES = frozenset(
    {
        "running",
        "completed",
        "partial",
        "insufficient_evidence",
        "failed",
        "interrupted",
    }
)


class HistoryConflict(Exception):
    """A terminal write contradicted the stored outcome. The row is unchanged."""


class ResearchHistory:
    """Create, finish, and read domain runs. Checkpoints are not queried."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_running(
        self,
        run_id: UUID,
        research_id: UUID,
        question: str,
        now: datetime,
        *,
        owner_id: UUID,
    ) -> None:
        """Insert one running row when this user still owns the scope."""
        moment = _aware(now)
        async with self._sessions() as session:
            async with session.begin():
                owned = await session.scalar(
                    select(ResearchScope.research_id)
                    .where(
                        ResearchScope.research_id == research_id,
                        ResearchScope.owner_user_id == owner_id,
                    )
                    .with_for_update()
                )
                if owned is None:
                    raise not_found()
                session.add(
                    ResearchRun(
                        run_id=run_id,
                        research_id=research_id,
                        question=question,
                        status="running",
                        error_code=None,
                        warnings=[],
                        usage=None,
                        created_at=moment,
                        started_at=moment,
                        finished_at=None,
                        updated_at=moment,
                    )
                )

    async def finalize_success(
        self,
        run_id: UUID,
        report: ResearchReport,
        warnings: list[str],
        usage: GraphUsage,
        now: datetime,
    ) -> None:
        """Commit a success status and its report together, or change nothing."""
        parsed = ResearchReport.model_validate(report)
        if parsed.status not in SUCCESS_STATUSES:
            raise HistoryConflict
        moment = _aware(now)
        payload = parsed.model_dump(mode="json")
        stored_warnings = sanitize_warnings(warnings)
        stored_usage = usage.model_dump(mode="json")
        async with self._sessions() as session:
            async with session.begin():
                run = await _lock_run(session, run_id)
                if run.status == parsed.status and await _same_success(
                    session, run_id, parsed
                ):
                    return
                if run.status != "running":
                    raise HistoryConflict
                run.status = parsed.status
                run.error_code = None
                run.warnings = stored_warnings
                run.usage = stored_usage
                run.finished_at = moment
                run.updated_at = moment
                session.add(
                    ResearchReportRecord(
                        run_id=run_id,
                        report=payload,
                        created_at=moment,
                    )
                )

    async def finalize_failure(
        self,
        run_id: UUID,
        code: str,
        *,
        interrupted: bool,
        now: datetime,
    ) -> None:
        """Commit a failed or interrupted status. This does not write a report."""
        moment = _aware(now)
        status, safe_code = _failure_outcome(code, interrupted=interrupted)
        async with self._sessions() as session:
            async with session.begin():
                run = await _lock_run(session, run_id)
                if run.status == status and run.error_code == safe_code:
                    return
                if run.status != "running":
                    raise HistoryConflict
                run.status = status
                run.error_code = safe_code
                run.finished_at = moment
                run.updated_at = moment

    async def list_runs(
        self,
        limit: int,
        cursor: str | None,
        *,
        owner_id: UUID,
    ) -> RunListResponse:
        """Return one keyset page for this owner. The report JSON is not selected."""
        if not 1 <= limit <= _LIST_LIMIT:
            raise invalid_request("limit")
        bound = decode_cursor(cursor) if cursor else None
        async with self._sessions() as session:
            async with session.begin():
                statement = (
                    select(ResearchRun)
                    .join(
                        ResearchScope,
                        ResearchScope.research_id == ResearchRun.research_id,
                    )
                    .where(ResearchScope.owner_user_id == owner_id)
                    .order_by(
                        ResearchRun.created_at.desc(),
                        ResearchRun.run_id.desc(),
                    )
                )
                if bound is not None:
                    created_at, run_id = bound
                    statement = statement.where(
                        or_(
                            ResearchRun.created_at < created_at,
                            and_(
                                ResearchRun.created_at == created_at,
                                ResearchRun.run_id < run_id,
                            ),
                        )
                    )
                rows = list(await session.scalars(statement.limit(limit + 1)))
                visible = rows[:limit]
                next_cursor = None
                if len(rows) > limit and visible:
                    last = visible[-1]
                    next_cursor = encode_cursor(last.created_at, last.run_id)
                items = [_summary(row) for row in visible]
        return RunListResponse(items=items, next_cursor=next_cursor)

    async def get_run(
        self,
        run_id: UUID,
        *,
        owner_id: UUID,
    ) -> RunDetailResponse | None:
        """Return one owned run. A corrupt report is an error, not a partial body."""
        async with self._sessions() as session:
            async with session.begin():
                run = await session.scalar(
                    select(ResearchRun)
                    .join(
                        ResearchScope,
                        ResearchScope.research_id == ResearchRun.research_id,
                    )
                    .where(
                        ResearchRun.run_id == run_id,
                        ResearchScope.owner_user_id == owner_id,
                    )
                )
                if run is None:
                    return None
                record = await session.get(ResearchReportRecord, run_id)
                report = _validated_report(run.status, record)
                return RunDetailResponse(run=_detail(run), report=report)


def sanitize_warnings(warnings: list[str]) -> list[str]:
    """Keep a bounded list of warning codes. Free text is dropped."""
    cleaned: list[str] = []
    for item in warnings:
        if len(cleaned) == _MAX_WARNINGS:
            break
        if _WARNING.fullmatch(item) and item not in cleaned:
            cleaned.append(item)
    return cleaned


def encode_cursor(created_at: datetime, run_id: UUID) -> str:
    """Encode a keyset position. The value is opaque to clients."""
    moment = _aware(created_at)
    body = json.dumps(
        {
            "v": 1,
            "created_at": moment.isoformat(),
            "run_id": str(run_id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, UUID]:
    """Reject a cursor that is not a versioned timestamp and run id."""
    try:
        if not value or any(char not in _CURSOR_ALPHABET for char in value):
            raise ValueError
        padded = value + ("=" * (-len(value) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != _CURSOR_KEYS:
            raise ValueError
        if payload["v"] != 1:
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


_CURSOR_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _failure_outcome(code: str, *, interrupted: bool) -> tuple[str, str]:
    if interrupted:
        if code not in _INTERRUPT_CODES:
            raise ValueError("interrupted runs use interrupted or deadline_exceeded")
        return "interrupted", code
    if code not in _FAILED_CODES:
        return "failed", "provider_unavailable"
    return "failed", code


async def _lock_run(session: AsyncSession, run_id: UUID) -> ResearchRun:
    statement = (
        select(ResearchRun).where(ResearchRun.run_id == run_id).with_for_update()
    )
    run = await session.scalar(statement)
    if run is None:
        raise HistoryConflict
    return run


async def _same_success(
    session: AsyncSession,
    run_id: UUID,
    report: ResearchReport,
) -> bool:
    stored = await session.get(ResearchReportRecord, run_id)
    if stored is None:
        return False
    try:
        current = ResearchReport.model_validate(stored.report)
    except ValidationError:
        return False
    return current.model_dump(mode="json") == report.model_dump(mode="json")


def _validated_report(
    status: str,
    record: ResearchReportRecord | None,
) -> ResearchReport | None:
    if status in SUCCESS_STATUSES:
        if record is None:
            raise invalid_state()
        try:
            report = ResearchReport.model_validate(record.report)
        except ValidationError:
            raise invalid_state() from None
        if report.status != status:
            raise invalid_state()
        return report
    if record is not None:
        raise invalid_state()
    if status not in _RUN_STATUSES:
        raise invalid_state()
    return None


def _summary(run: ResearchRun) -> RunSummary:
    return RunSummary(
        run_id=run.run_id,
        research_id=run.research_id,
        question=run.question,
        status=_status(run.status),
        error_code=run.error_code,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _detail(run: ResearchRun) -> RunDetail:
    usage = None
    if run.usage is not None:
        try:
            usage = GraphUsage.model_validate(run.usage)
        except ValidationError:
            raise invalid_state() from None
    if run.status in SUCCESS_STATUSES and usage is None:
        raise invalid_state()
    return RunDetail(
        run_id=run.run_id,
        research_id=run.research_id,
        question=run.question,
        status=_status(run.status),
        error_code=run.error_code,
        warnings=_warning_list(run.warnings),
        usage=usage,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        updated_at=run.updated_at,
    )


def _warning_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise invalid_state()
    return list(value)


def _status(value: str) -> RunStatus:
    if value not in _RUN_STATUSES:
        raise invalid_state()
    return cast(RunStatus, value)
