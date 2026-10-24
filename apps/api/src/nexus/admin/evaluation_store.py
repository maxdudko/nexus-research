"""Insert-only evaluation history. Completed rows are not updated."""

import base64
import json
import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.admin.schemas import (
    EvaluationCasePage,
    EvaluationDetail,
    EvaluationListItem,
    EvaluationListResponse,
    EvaluationRunInsert,
    LiveConfig,
    LiveSummary,
    OfflineConfig,
    OfflineSummary,
    StoredCase,
    SuiteName,
    case_matches,
)
from nexus.ai.errors import invalid_request
from nexus.models.evaluation import EvaluationCaseResult, EvaluationRun

_CURSOR_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
_LIST_KEYS = frozenset({"v", "imported_at", "id"})
_CASE_KEYS = frozenset({"v", "category", "case_id"})
_CASE_ID = r"^[a-z0-9-]{1,64}$"
_CATEGORIES = frozenset({"retrieval", "citation", "grounding"})
_MAX_CURSOR = 512


class EvaluationConflict(Exception):
    """The artifact hash is already stored with different metadata."""


class EvaluationUnreadable(Exception):
    """Stored JSON failed the allowlist. The payload is not included."""


def metadata_key(payload: EvaluationRunInsert) -> str:
    """Canonical metadata. Identity and import time are not part of equality."""
    dumped = payload.model_dump(mode="json")
    dumped.pop("id", None)
    dumped.pop("imported_at", None)
    cases = dumped.get("cases")
    if isinstance(cases, list):
        dumped["cases"] = sorted(
            cases,
            key=lambda item: (str(item["category"]), str(item["case_id"])),
        )
    return json.dumps(dumped, sort_keys=True, separators=(",", ":"))


async def insert_evaluation(
    factory: async_sessionmaker[AsyncSession],
    payload: EvaluationRunInsert,
) -> UUID:
    """Insert one run and its cases, or return the existing id for the same bytes."""
    try:
        return await _insert_once(factory, payload)
    except IntegrityError as exc:
        if _sqlstate(exc) != "23505":
            raise
    stored = await _load(factory, payload.artifact_sha256)
    if stored is None or metadata_key(stored) != metadata_key(payload):
        raise EvaluationConflict
    return stored.id


async def _insert_once(
    factory: async_sessionmaker[AsyncSession],
    payload: EvaluationRunInsert,
) -> UUID:
    async with factory() as session, session.begin():
        existing = await session.scalar(
            select(EvaluationRun).where(
                EvaluationRun.artifact_sha256 == payload.artifact_sha256
            )
        )
        if existing is not None:
            stored = await _read(session, existing)
            if stored is None or metadata_key(stored) != metadata_key(payload):
                raise EvaluationConflict
            return existing.id
        session.add(_run(payload))
        await session.flush()
        session.add_all(_cases(payload))
        await session.flush()
        return payload.id


async def _load(
    factory: async_sessionmaker[AsyncSession],
    artifact_sha256: str,
) -> EvaluationRunInsert | None:
    async with factory() as session:
        existing = await session.scalar(
            select(EvaluationRun).where(
                EvaluationRun.artifact_sha256 == artifact_sha256
            )
        )
        if existing is None:
            return None
        return await _read(session, existing)


async def _read(
    session: AsyncSession,
    run: EvaluationRun,
) -> EvaluationRunInsert | None:
    rows = list(
        await session.scalars(
            select(EvaluationCaseResult)
            .where(EvaluationCaseResult.evaluation_run_id == run.id)
            .order_by(EvaluationCaseResult.category, EvaluationCaseResult.case_id)
        )
    )
    try:
        return EvaluationRunInsert.model_validate(
            {
                "id": run.id,
                "suite": run.suite,
                "outcome": run.outcome,
                "artifact_sha256": run.artifact_sha256,
                "dataset_version": run.dataset_version,
                "dataset_sha256": run.dataset_sha256,
                "report_version": run.report_version,
                "scorer_version": run.scorer_version,
                "k": run.k,
                "code_revision": run.code_revision,
                "code_revision_source": run.code_revision_source,
                "model": run.model,
                "prompt_version": run.prompt_version,
                "temperature": run.temperature,
                "pricing_version": run.pricing_version,
                "executed_at": run.executed_at,
                "imported_at": run.imported_at,
                "provenance_level": run.provenance_level,
                "is_committed_baseline": run.is_committed_baseline,
                "summary": run.summary,
                "config": run.config,
                "cases": [
                    {
                        "category": row.category,
                        "case_id": row.case_id,
                        "result": row.result,
                    }
                    for row in rows
                ],
            }
        )
    except ValueError:
        return None


def _run(payload: EvaluationRunInsert) -> EvaluationRun:
    return EvaluationRun(
        id=payload.id,
        suite=payload.suite,
        outcome=payload.outcome,
        artifact_sha256=payload.artifact_sha256,
        dataset_version=payload.dataset_version,
        dataset_sha256=payload.dataset_sha256,
        report_version=payload.report_version,
        scorer_version=payload.scorer_version,
        k=payload.k,
        code_revision=payload.code_revision,
        code_revision_source=payload.code_revision_source,
        model=payload.model,
        prompt_version=payload.prompt_version,
        temperature=payload.temperature,
        pricing_version=payload.pricing_version,
        executed_at=None,
        imported_at=payload.imported_at,
        provenance_level=payload.provenance_level,
        is_committed_baseline=payload.is_committed_baseline,
        summary=_object(payload.summary),
        config=_object(payload.config),
    )


def _cases(payload: EvaluationRunInsert) -> list[EvaluationCaseResult]:
    return [
        EvaluationCaseResult(
            evaluation_run_id=payload.id,
            category=case.category,
            case_id=case.case_id,
            result=_object(case.result),
        )
        for case in payload.cases
    ]


def _object(model: object) -> dict[str, object]:
    from pydantic import BaseModel

    if not isinstance(model, BaseModel):
        raise TypeError("evaluation payload must be an object")
    dumped = model.model_dump(mode="json")
    if not isinstance(dumped, dict):
        raise TypeError("evaluation payload must be an object")
    return dumped


async def list_evaluations(
    factory: async_sessionmaker[AsyncSession],
    limit: int,
    cursor: str | None,
) -> EvaluationListResponse:
    """Return one history page. Case rows are not selected."""
    if not 1 <= limit <= 50:
        raise invalid_request("limit")
    bound = decode_evaluation_cursor(cursor) if cursor else None
    async with factory() as session:
        statement = select(EvaluationRun).order_by(
            EvaluationRun.imported_at.desc(),
            EvaluationRun.id.desc(),
        )
        if bound is not None:
            imported_at, evaluation_id = bound
            statement = statement.where(
                or_(
                    EvaluationRun.imported_at < imported_at,
                    and_(
                        EvaluationRun.imported_at == imported_at,
                        EvaluationRun.id < evaluation_id,
                    ),
                )
            )
        rows = list(await session.scalars(statement.limit(limit + 1)))
        visible = rows[:limit]
        items = [_list_item(_detail(row)) for row in visible]
        next_cursor = None
        if len(rows) > limit and visible:
            last = visible[-1]
            next_cursor = encode_evaluation_cursor(last.imported_at, last.id)
    return EvaluationListResponse(items=items, next_cursor=next_cursor)


async def get_evaluation(
    factory: async_sessionmaker[AsyncSession],
    evaluation_id: UUID,
) -> EvaluationDetail | None:
    """Return one run. A missing id is None. Invalid JSON is unreadable."""
    async with factory() as session:
        run = await session.get(EvaluationRun, evaluation_id)
        if run is None:
            return None
        return _detail(run)


async def list_cases(
    factory: async_sessionmaker[AsyncSession],
    evaluation_id: UUID,
    limit: int,
    cursor: str | None,
    category: str | None,
) -> EvaluationCasePage | None:
    """Return one case page. None means the evaluation id is missing."""
    if not 1 <= limit <= 50:
        raise invalid_request("limit")
    bound = decode_case_cursor(cursor) if cursor else None
    if category is not None and bound is not None and bound[0] != category:
        raise invalid_request("cursor")
    async with factory() as session:
        suite = await session.scalar(
            select(EvaluationRun.suite).where(EvaluationRun.id == evaluation_id)
        )
        if suite is None:
            return None
        if suite not in ("offline", "live"):
            raise EvaluationUnreadable
        statement = (
            select(
                EvaluationCaseResult.category,
                EvaluationCaseResult.case_id,
                EvaluationCaseResult.result,
            )
            .where(EvaluationCaseResult.evaluation_run_id == evaluation_id)
            .order_by(
                EvaluationCaseResult.category.asc(),
                EvaluationCaseResult.case_id.asc(),
            )
        )
        if category is not None:
            statement = statement.where(EvaluationCaseResult.category == category)
        if bound is not None:
            cursor_category, case_id = bound
            statement = statement.where(
                or_(
                    EvaluationCaseResult.category > cursor_category,
                    and_(
                        EvaluationCaseResult.category == cursor_category,
                        EvaluationCaseResult.case_id > case_id,
                    ),
                )
            )
        rows = (await session.execute(statement.limit(limit + 1))).all()
    visible = rows[:limit]
    items = [_case(suite, row.category, row.case_id, row.result) for row in visible]
    next_cursor = None
    if len(rows) > limit and visible:
        last = visible[-1]
        next_cursor = encode_case_cursor(str(last.category), str(last.case_id))
    return EvaluationCasePage(items=items, next_cursor=next_cursor)


def encode_evaluation_cursor(imported_at: datetime, evaluation_id: UUID) -> str:
    """Encode a history keyset. The value is opaque to clients."""
    return _encode(
        {
            "v": 1,
            "imported_at": _aware(imported_at).isoformat(),
            "id": str(evaluation_id),
        }
    )


def decode_evaluation_cursor(value: str) -> tuple[datetime, UUID]:
    """Reject a cursor that is not a versioned import time and id."""
    payload = _decode(value, _LIST_KEYS)
    imported_text = payload["imported_at"]
    identifier = payload["id"]
    if not isinstance(imported_text, str) or not isinstance(identifier, str):
        raise invalid_request("cursor")
    try:
        imported_at = datetime.fromisoformat(imported_text)
        if imported_at.tzinfo is None:
            raise ValueError
        evaluation_id = UUID(identifier)
        if str(evaluation_id) != identifier:
            raise ValueError
    except ValueError:
        raise invalid_request("cursor") from None
    return imported_at, evaluation_id


def encode_case_cursor(category: str, case_id: str) -> str:
    """Encode a case keyset."""
    return _encode({"v": 1, "category": category, "case_id": case_id})


def decode_case_cursor(value: str) -> tuple[str, str]:
    """Reject a cursor that is not a versioned category and case id."""
    payload = _decode(value, _CASE_KEYS)
    category = payload["category"]
    case_id = payload["case_id"]
    if (
        not isinstance(category, str)
        or category not in _CATEGORIES
        or not isinstance(case_id, str)
        or re.fullmatch(_CASE_ID, case_id) is None
    ):
        raise invalid_request("cursor")
    return category, case_id


def _detail(run: EvaluationRun) -> EvaluationDetail:
    try:
        if run.suite == "live":
            return _live_detail(run)
        if run.suite == "offline":
            return _offline_detail(run)
        raise EvaluationUnreadable
    except ValidationError, EvaluationUnreadable:
        raise EvaluationUnreadable from None


def _common(run: EvaluationRun) -> dict[str, object]:
    return {
        "id": run.id,
        "suite": run.suite,
        "outcome": run.outcome,
        "imported_at": run.imported_at,
        "executed_at": None,
        "dataset_version": run.dataset_version,
        "dataset_sha256": run.dataset_sha256,
        "report_version": run.report_version,
        "scorer_version": run.scorer_version,
        "k": run.k,
        "code_revision": run.code_revision,
        "code_revision_source": run.code_revision_source,
        "provenance_level": run.provenance_level,
        "is_committed_baseline": run.is_committed_baseline,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "temperature": _temperature_text(run.temperature),
        "pricing_version": run.pricing_version,
    }


def _live_detail(run: EvaluationRun) -> EvaluationDetail:
    summary = LiveSummary.model_validate(run.summary)
    config = LiveConfig.model_validate(run.config)
    return EvaluationDetail.model_validate(
        {
            **_common(run),
            "retrieval": summary.retrieval,
            "citations": summary.citations,
            "grounding": summary.grounding,
            "counts": summary.counts,
            "agreement": summary.calibration.agreement,
            "compared": summary.calibration.compared,
            "agreements": summary.calibration.agreements,
            "config": config,
            "calibration": summary.calibration,
        }
    )


def _offline_detail(run: EvaluationRun) -> EvaluationDetail:
    summary = OfflineSummary.model_validate(run.summary)
    config = OfflineConfig.model_validate(run.config)
    return EvaluationDetail.model_validate(
        {
            **_common(run),
            "retrieval": summary.retrieval,
            "citations": summary.citations,
            "grounding": summary.grounding,
            "counts": None,
            "agreement": None,
            "compared": None,
            "agreements": None,
            "config": config,
            "calibration": None,
        }
    )


def _list_item(detail: EvaluationDetail) -> EvaluationListItem:
    dumped = detail.model_dump()
    dumped.pop("config", None)
    dumped.pop("calibration", None)
    try:
        return EvaluationListItem.model_validate(dumped)
    except ValidationError:
        raise EvaluationUnreadable from None


def _case(
    suite: str,
    category: object,
    case_id: object,
    result: object,
) -> StoredCase:
    if suite not in ("offline", "live"):
        raise EvaluationUnreadable
    try:
        case = StoredCase.model_validate(
            {"category": category, "case_id": case_id, "result": result}
        )
    except ValidationError:
        raise EvaluationUnreadable from None
    checked: SuiteName = "live" if suite == "live" else "offline"
    if not case_matches(checked, case):
        raise EvaluationUnreadable
    return case


def _temperature_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.001")), "f")


def _encode(payload: dict[str, object]) -> str:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def _decode(value: str, keys: frozenset[str]) -> dict[str, object]:
    try:
        if (
            not value
            or len(value) > _MAX_CURSOR
            or any(char not in _CURSOR_ALPHABET for char in value)
        ):
            raise ValueError
        padded = value + ("=" * (-len(value) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or set(payload) != keys
            or payload.get("v") != 1
        ):
            raise ValueError
        if isinstance(payload.get("v"), bool):
            raise ValueError
    except ValueError, json.JSONDecodeError, UnicodeError:
        raise invalid_request("cursor") from None
    return payload


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _sqlstate(exc: IntegrityError) -> str | None:
    state = getattr(exc.orig, "sqlstate", None)
    if isinstance(state, str):
        return state
    return None
