"""Evaluation repository. Rows are inserted once."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from tests.integration.conftest import run_alembic

from nexus.admin.evaluation_store import EvaluationConflict, insert_evaluation
from nexus.admin.schemas import EvaluationRunInsert
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.models.evaluation import EvaluationCaseResult, EvaluationRun

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
ARTIFACT = "ab" * 32
DATASET = "cd" * 32


def _payload(**overrides: object) -> EvaluationRunInsert:
    values: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "suite": "offline",
        "outcome": "passed",
        "artifact_sha256": ARTIFACT,
        "dataset_version": "2026-10-19.2",
        "dataset_sha256": DATASET,
        "report_version": "1",
        "scorer_version": "1",
        "k": 5,
        "imported_at": NOW.isoformat(),
        "provenance_level": "offline_recomputed",
        "is_committed_baseline": False,
        "summary": {
            "retrieval": {
                "recall_at_k": "0.833333",
                "precision_at_k": "0.240000",
                "mrr_at_k": "0.705556",
                "defined_cases": 15,
                "undefined_cases": 1,
            },
            "citations": {"cases": 1, "valid": 1, "invalid": 0},
            "grounding": {
                "cases": 1,
                "structurally_valid": 1,
                "structurally_invalid": 0,
            },
        },
        "config": {"empty_gold": "undefined", "metric_quant": "0.000001"},
        "cases": [
            {
                "category": "retrieval",
                "case_id": "q01-single",
                "result": {
                    "gold_count": 1,
                    "recall_at_k": "1.000000",
                    "precision_at_k": "0.200000",
                    "mrr_at_k": "1.000000",
                },
            }
        ],
    }
    values.update(overrides)
    return EvaluationRunInsert.model_validate(values)


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


async def test_reimport_returns_the_same_id_without_changing_import_time(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    first = _payload()
    second = _payload(imported_at=(NOW + timedelta(hours=1)).isoformat())
    try:
        stored = await insert_evaluation(factory, first)
        again = await insert_evaluation(factory, second)
        async with factory() as session:
            row = await session.get(EvaluationRun, stored)
            case_count = await session.scalar(
                select(func.count()).select_from(EvaluationCaseResult)
            )
    finally:
        await engine.dispose()
    assert again == stored
    assert row is not None
    assert row.imported_at == NOW
    assert case_count == 1


async def test_conflicting_metadata_leaves_the_stored_row_unchanged(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    first = _payload()
    try:
        stored = await insert_evaluation(factory, first)
        with pytest.raises(EvaluationConflict):
            await insert_evaluation(
                factory,
                _payload(is_committed_baseline=True),
            )
        async with factory() as session:
            row = await session.get(EvaluationRun, stored)
    finally:
        await engine.dispose()
    assert row is not None
    assert row.is_committed_baseline is False


async def test_repository_rejects_rationale_before_writing(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    raw = _payload().model_dump(mode="json")
    raw["cases"][0]["result"]["rationale"] = "SENTINEL-rationale"
    try:
        with pytest.raises(ValidationError):
            await insert_evaluation(factory, EvaluationRunInsert.model_validate(raw))
        async with factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(EvaluationRun)
            )
    finally:
        await engine.dispose()
    assert count == 0


async def test_concurrent_inserts_of_one_hash_leave_one_row(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    left = _payload()
    right = _payload(id=str(uuid.uuid4()))
    try:
        stored = await asyncio.gather(
            insert_evaluation(factory, left),
            insert_evaluation(factory, right),
        )
        async with factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(EvaluationRun)
            )
    finally:
        await engine.dispose()
    assert stored[0] == stored[1]
    assert count == 1
