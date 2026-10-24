"""Evaluation history schema. Research tables stay in place."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.integration.conftest import run_alembic

from nexus.infrastructure.database import create_db_engine
from nexus.models.user import QUARANTINE_USER_ID

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
ARTIFACT = "a" * 64
DATASET = "b" * 64
QUESTION = "How does evaluation migration behave?"


async def _upgrade(database_url: str, revision: str = "head") -> None:
    completed = await run_alembic(database_url, "upgrade", revision)
    assert completed.returncode == 0, completed.stderr


def _run_values(run_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": run_id,
        "suite": "offline",
        "outcome": "passed",
        "artifact_sha256": ARTIFACT,
        "dataset_version": "2026-10-19.2",
        "dataset_sha256": DATASET,
        "report_version": "1",
        "scorer_version": "1",
        "k": 5,
        "code_revision": None,
        "code_revision_source": None,
        "model": None,
        "prompt_version": None,
        "temperature": None,
        "pricing_version": None,
        "executed_at": None,
        "imported_at": NOW,
        "provenance_level": "offline_recomputed",
        "is_committed_baseline": False,
        "summary": "{}",
        "config": "{}",
    }
    values.update(overrides)
    return values


_RUN_COLUMNS = (
    "id",
    "suite",
    "outcome",
    "artifact_sha256",
    "dataset_version",
    "dataset_sha256",
    "report_version",
    "scorer_version",
    "k",
    "code_revision",
    "code_revision_source",
    "model",
    "prompt_version",
    "temperature",
    "pricing_version",
    "executed_at",
    "imported_at",
    "provenance_level",
    "is_committed_baseline",
    "summary",
    "config",
)


async def _insert_run(
    connection: AsyncConnection,
    run_id: uuid.UUID,
    **overrides: object,
) -> None:
    values = _run_values(run_id, **overrides)
    assignments = ", ".join(
        "CAST(:summary AS jsonb)"
        if name == "summary"
        else "CAST(:config AS jsonb)"
        if name == "config"
        else f":{name}"
        for name in _RUN_COLUMNS
    )
    columns = ", ".join(_RUN_COLUMNS)
    await connection.execute(
        text(f"INSERT INTO evaluation_runs ({columns}) VALUES ({assignments})"),
        values,
    )


async def test_evaluation_upgrade_keeps_research_and_downgrade_drops_history(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url, "e3b7d91c4a20")
    engine = create_db_engine(test_database_url)
    run_id = uuid.uuid4()
    research_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO research_scopes (research_id, owner_user_id) "
                    "VALUES (:research_id, :owner)"
                ),
                {"research_id": research_id, "owner": QUARANTINE_USER_ID},
            )
            await connection.execute(
                text(
                    "INSERT INTO research_runs "
                    "(run_id, research_id, question, status) "
                    "VALUES (:run_id, :research_id, :question, 'running')"
                ),
                {
                    "run_id": run_id,
                    "research_id": research_id,
                    "question": QUESTION,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO research_reports (run_id, report) "
                    "VALUES (:run_id, CAST(:report AS jsonb))"
                ),
                {"run_id": run_id, "report": '{"title":"kept"}'},
            )
    finally:
        await engine.dispose()

    await _upgrade(test_database_url)
    repeated = await run_alembic(test_database_url, "upgrade", "head")
    assert repeated.returncode == 0, repeated.stderr
    checked = await run_alembic(test_database_url, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr

    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            question = await connection.scalar(
                text("SELECT question FROM research_runs WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
            report = await connection.scalar(
                text(
                    "SELECT report->>'title' FROM research_reports "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            tables = set(
                await connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
    finally:
        await engine.dispose()
    assert question == QUESTION
    assert report == "kept"
    assert "evaluation_runs" in tables
    assert "evaluation_case_results" in tables

    downgraded = await run_alembic(test_database_url, "downgrade", "e3b7d91c4a20")
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            question = await connection.scalar(
                text("SELECT question FROM research_runs WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
            names = set(
                await connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
    finally:
        await engine.dispose()
    assert question == QUESTION
    assert "evaluation_runs" not in names
    assert "evaluation_case_results" not in names
    assert "research_runs" in names
    assert "research_reports" in names
    assert "research_scopes" in names


async def test_evaluation_checks_reject_inconsistent_rows(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            with pytest.raises(IntegrityError):
                async with connection.begin():
                    await _insert_run(
                        connection,
                        uuid.uuid4(),
                        suite="live",
                        outcome="informational",
                        provenance_level="operator_imported",
                        model=None,
                        prompt_version=None,
                        temperature=None,
                        pricing_version=None,
                    )
            with pytest.raises(IntegrityError):
                async with connection.begin():
                    await _insert_run(connection, uuid.uuid4(), model="gpt-4o-mini")
            with pytest.raises(IntegrityError):
                async with connection.begin():
                    await _insert_run(
                        connection,
                        uuid.uuid4(),
                        suite="live",
                        outcome="informational",
                        provenance_level="operator_imported",
                        model="gpt-4o-mini",
                        prompt_version="grounding-judge-2",
                        temperature="0.000",
                        pricing_version="2026-10-19",
                        is_committed_baseline=True,
                    )
            with pytest.raises(IntegrityError):
                async with connection.begin():
                    await _insert_run(
                        connection,
                        uuid.uuid4(),
                        code_revision="a" * 40,
                        code_revision_source=None,
                    )
    finally:
        await engine.dispose()


async def test_evaluation_hash_and_case_key_are_unique_and_cases_cascade(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    run_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await _insert_run(connection, run_id)
            await connection.execute(
                text(
                    "INSERT INTO evaluation_case_results "
                    "(evaluation_run_id, category, case_id, result) "
                    "VALUES (:run_id, 'retrieval', 'q01-single', "
                    "CAST(:result AS jsonb))"
                ),
                {"run_id": run_id, "result": '{"gold_count":1}'},
            )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await _insert_run(
                    connection,
                    uuid.uuid4(),
                    artifact_sha256=ARTIFACT,
                )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO evaluation_case_results "
                        "(evaluation_run_id, category, case_id, result) "
                        "VALUES (:run_id, 'retrieval', 'q01-single', '{}'::jsonb)"
                    ),
                    {"run_id": run_id},
                )
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM evaluation_runs WHERE id = :run_id"),
                {"run_id": run_id},
            )
            remaining = await connection.scalar(
                text(
                    "SELECT count(*) FROM evaluation_case_results "
                    "WHERE evaluation_run_id = :run_id"
                ),
                {"run_id": run_id},
            )
    finally:
        await engine.dispose()
    assert remaining == 0
