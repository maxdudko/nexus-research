"""Import CLI against PostgreSQL."""

import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from tests.conftest import make_settings
from tests.integration.conftest import run_alembic

from nexus.admin.errors import (
    ARTIFACT_INVALID,
    LIVE_MISMATCH,
    METADATA_CONFLICT,
    STORE_UNAVAILABLE,
    VERSION_UNSUPPORTED,
)
from nexus.admin.evaluation_import import run_cli
from nexus.infrastructure.database import create_db_engine
from nexus.models.evaluation import EvaluationCaseResult, EvaluationRun

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[4]
BASELINE = ROOT / "docs" / "evaluation" / "baseline.json"
LIVE = ROOT / "docs" / "evaluation" / "outputs" / "nexus-eval-live.json"


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


def _settings(database_url: str):
    return make_settings(environment="test", database_url=database_url)


async def _run(argv: list[str], settings: object) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = await asyncio.to_thread(
        run_cli,
        argv,
        stdout=stdout,
        stderr=stderr,
        settings=settings,
    )
    return code, stdout.getvalue().strip(), stderr.getvalue()


async def test_baseline_import_is_idempotent_and_live_import_stores_no_rationale(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    settings = _settings(test_database_url)
    code, stored, stderr = await _run(
        ["--artifact", str(BASELINE), "--committed-baseline"],
        settings,
    )
    assert code == 0, stderr
    assert stderr == ""
    again, same, again_err = await _run(
        ["--artifact", str(BASELINE), "--committed-baseline"],
        settings,
    )
    assert again == 0, again_err
    assert same == stored

    conflict, _, conflict_err = await _run(["--artifact", str(BASELINE)], settings)
    assert conflict == 1
    assert conflict_err.strip() == METADATA_CONFLICT

    live_code, _, live_err = await _run(["--artifact", str(LIVE)], settings)
    assert live_code == 0, live_err

    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            baseline = await connection.execute(
                text(
                    "SELECT outcome, provenance_level, is_committed_baseline, "
                    "executed_at FROM evaluation_runs WHERE id = :id"
                ),
                {"id": stored},
            )
            row = baseline.one()
            counts = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT category, count(*) FROM evaluation_case_results "
                            "WHERE evaluation_run_id = :id GROUP BY category"
                        ),
                        {"id": stored},
                    )
                ).all()
            )
            results = list(
                await connection.scalars(select(EvaluationCaseResult.result))
            )
    finally:
        await engine.dispose()
    assert row.outcome == "passed"
    assert row.provenance_level == "offline_recomputed"
    assert row.is_committed_baseline is True
    assert row.executed_at is None
    assert counts == {"retrieval": 16, "citation": 10, "grounding": 15}
    blob = json.dumps(results)
    artifact = json.loads(LIVE.read_text(encoding="utf-8"))
    for case in artifact["grounding"]:
        rationale = case.get("rationale")
        if isinstance(rationale, str):
            assert rationale not in blob
    assert '"rationale"' not in blob


async def test_rejected_imports_write_nothing(
    test_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _upgrade(test_database_url)
    settings = _settings(test_database_url)
    forged_body = json.loads(LIVE.read_text(encoding="utf-8"))
    forged_body["calibration"]["agreements"] = 0
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(forged_body), encoding="utf-8")
    version = tmp_path / "version.json"
    version.write_text('{"report_version":"9"}\n', encoding="utf-8")
    truncated = tmp_path / "truncated.json"
    truncated.write_text("{", encoding="utf-8")

    forged_code, _, forged_err = await _run(["--artifact", str(forged)], settings)
    version_code, _, version_err = await _run(["--artifact", str(version)], settings)
    truncated_code, _, truncated_err = await _run(
        ["--artifact", str(truncated)],
        settings,
    )
    assert forged_code == 1
    assert forged_err.strip() == LIVE_MISMATCH
    assert version_code == 1
    assert version_err.strip() == VERSION_UNSUPPORTED
    assert truncated_code == 1
    assert truncated_err.strip() == ARTIFACT_INVALID

    async def fail(_factory: object, _payload: object) -> None:
        raise RuntimeError("postgresql://super-secret")

    monkeypatch.setattr("nexus.admin.evaluation_import.insert_evaluation", fail)
    failed, _, failed_err = await _run(
        ["--artifact", str(BASELINE), "--committed-baseline"],
        settings,
    )
    assert failed == 1
    assert failed_err.strip() == STORE_UNAVAILABLE
    assert "super-secret" not in failed_err

    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(
                select(func.count()).select_from(EvaluationRun)
            )
    finally:
        await engine.dispose()
    assert count == 0


async def test_concurrent_imports_leave_one_run(test_database_url: str) -> None:
    await _upgrade(test_database_url)
    settings = _settings(test_database_url)
    argv = ["--artifact", str(BASELINE), "--committed-baseline"]
    first, second = await asyncio.gather(
        _run(argv, settings),
        _run(argv, settings),
    )
    assert first[0] == 0, first[2]
    assert second[0] == 0, second[2]
    assert first[1] == second[1]
    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(
                select(func.count()).select_from(EvaluationRun)
            )
    finally:
        await engine.dispose()
    assert count == 1
