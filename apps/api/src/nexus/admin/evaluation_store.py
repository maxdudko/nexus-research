"""Insert-only evaluation history. Completed rows are not updated."""

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.admin.schemas import EvaluationRunInsert
from nexus.models.evaluation import EvaluationCaseResult, EvaluationRun


class EvaluationConflict(Exception):
    """The artifact hash is already stored with different metadata."""


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


def _sqlstate(exc: IntegrityError) -> str | None:
    state = getattr(exc.orig, "sqlstate", None)
    if isinstance(state, str):
        return state
    return None
