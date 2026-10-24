"""Protected evaluation reads against PostgreSQL."""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import text
from tests.conftest import client_for, make_settings
from tests.integration.conftest import run_alembic

from nexus.admin.evaluation_store import encode_case_cursor, insert_evaluation
from nexus.admin.normalization import prepare_import
from nexus.admin.schemas import EvaluationRunInsert
from nexus.identity.cli import run_cli
from nexus.identity.store import get_session_by_hash, revoke_session
from nexus.identity.tokens import token_hash
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.main import create_app
from nexus.models.user import QUARANTINE_USER_ID

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[4]
LIVE = ROOT / "docs" / "evaluation" / "outputs" / "nexus-eval-live.json"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
PASSWORD = "correct-horse-1"
PEPPER = "integration-test-pepper-0123456789abcd"
ORIGIN = {"origin": "http://127.0.0.1:3000"}
ADMIN = "canary-admin@example.com"
USER = "canary-user@example.com"
QUESTION = "Canary question about private research sources."
REPORT = "Canary report sentence stays private."
RATIONALE = "SENTINEL_RATIONALE"
DATASET_A = "aa" * 32
DATASET_B = "bb" * 32
MISSING = "99999999-9999-4999-8999-999999999999"
NOT_FOUND = {"error": {"code": "not_found", "message": "The evaluation was not found."}}


def _settings(database_url: str):
    return make_settings(
        environment="test",
        database_url=database_url,
        session_pepper=PEPPER,
    )


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


async def _cli(database_url: str, argv: list[str]) -> None:
    code = await asyncio.to_thread(
        run_cli,
        argv,
        stdin=StringIO(PASSWORD + "\n"),
        stderr=StringIO(),
        settings=_settings(database_url),
    )
    assert code == 0


def _payload(**overrides: object) -> EvaluationRunInsert:
    values: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "suite": "offline",
        "outcome": "passed",
        "artifact_sha256": "11" * 32,
        "dataset_version": "2026-10-19.2",
        "dataset_sha256": DATASET_A,
        "report_version": "1",
        "scorer_version": "1",
        "k": 5,
        "code_revision": "a" * 40,
        "code_revision_source": "operator_asserted",
        "imported_at": NOW.isoformat(),
        "provenance_level": "offline_recomputed",
        "is_committed_baseline": False,
        "summary": {
            "retrieval": {
                "recall_at_k": "0.100000",
                "precision_at_k": "0.250000",
                "mrr_at_k": None,
                "defined_cases": 2,
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


def _summary(
    recall: str,
    mrr: str | None,
    valid: int,
    invalid: int,
) -> dict[str, object]:
    return {
        "retrieval": {
            "recall_at_k": recall,
            "precision_at_k": "0.250000",
            "mrr_at_k": mrr,
            "defined_cases": 2 if recall == "0.100000" else 4,
            "undefined_cases": 1,
        },
        "citations": {"cases": valid + invalid, "valid": valid, "invalid": invalid},
        "grounding": {
            "cases": 1,
            "structurally_valid": 1,
            "structurally_invalid": 0,
        },
    }


async def _login(client, email: str) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def _absent(body: str) -> None:
    assert QUESTION not in body
    assert REPORT not in body
    assert ADMIN not in body
    assert USER not in body
    assert RATIONALE not in body
    artifact = json.loads(LIVE.read_text(encoding="utf-8"))
    for row in artifact["grounding"]:
        rationale = row.get("rationale")
        if isinstance(rationale, str):
            assert rationale not in body


async def test_evaluation_api_is_private_and_comparable(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    await _cli(test_database_url, ["bootstrap-admin", ADMIN])
    await _cli(test_database_url, ["create-user", USER])
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    research_run = uuid.uuid4()
    left = _payload(
        artifact_sha256="11" * 32,
        imported_at=NOW.isoformat(),
        summary=_summary("0.100000", None, 1, 0),
    )
    right = _payload(
        artifact_sha256="22" * 32,
        code_revision="b" * 40,
        imported_at=(NOW + timedelta(hours=1)).isoformat(),
        summary=_summary("0.400000", "0.500000", 3, 1),
    )
    other = _payload(
        artifact_sha256="33" * 32,
        dataset_sha256=DATASET_B,
        code_revision=None,
        code_revision_source=None,
        imported_at=(NOW + timedelta(hours=2)).isoformat(),
        summary=_summary("0.100000", None, 1, 0),
    )
    raw = LIVE.read_bytes()
    live = prepare_import(
        raw,
        fixtures=None,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        code_revision=None,
        code_revision_source=None,
        committed_baseline=False,
        baseline_bytes=None,
        imported_at=NOW + timedelta(days=1),
        run_id=uuid.uuid4(),
    )
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
                    "run_id": research_run,
                    "research_id": research_id,
                    "question": QUESTION,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO research_reports (run_id, report) "
                    "VALUES (:run_id, CAST(:report AS jsonb))"
                ),
                {"run_id": research_run, "report": json.dumps({"body": REPORT})},
            )
        await insert_evaluation(factory, left)
        await insert_evaluation(factory, right)
        await insert_evaluation(factory, other)
        await insert_evaluation(factory, live)
        app = create_app(_settings(test_database_url))
        async with client_for(app) as client:
            anonymous = await client.get("/api/v1/admin/evaluations")
            compare_anon = await client.get("/api/v1/admin/evaluations/compare")
            assert anonymous.status_code == compare_anon.status_code == 401
            assert anonymous.headers["cache-control"] == "no-store"
            assert compare_anon.json()["error"]["code"] == "unauthenticated"

            await _login(client, USER)
            blocked = await client.get("/api/v1/admin/evaluations")
            assert blocked.status_code == 403
            assert blocked.headers["cache-control"] == "no-store"
            assert blocked.json()["error"]["code"] == "forbidden"
            client.cookies.clear()

            await _login(client, ADMIN)
            first = await client.get("/api/v1/admin/evaluations", params={"limit": "2"})
            again = await client.get("/api/v1/admin/evaluations", params={"limit": "2"})
            assert first.status_code == 200
            assert first.headers["cache-control"] == "no-store"
            assert first.text == again.text
            page = first.json()
            assert [item["id"] for item in page["items"]] == [
                str(live.id),
                str(other.id),
            ]
            assert "matrix" not in first.text
            older = await client.get(
                "/api/v1/admin/evaluations",
                params={"limit": "2", "cursor": page["next_cursor"]},
            )
            assert [item["id"] for item in older.json()["items"]] == [
                str(right.id),
                str(left.id),
            ]
            assert older.json()["next_cursor"] is None
            bad_cursor = await client.get(
                "/api/v1/admin/evaluations",
                params={"cursor": "nope"},
            )
            assert bad_cursor.status_code == 422
            assert bad_cursor.json()["error"]["message"] == "cursor: invalid value"
            assert "nope" not in bad_cursor.text

            precedence = await client.get("/api/v1/admin/evaluations/compare")
            assert precedence.status_code == 422
            assert precedence.json()["error"]["message"] == "left_id: invalid value"
            missing = await client.get(f"/api/v1/admin/evaluations/{MISSING}")
            assert missing.status_code == 404
            assert missing.headers["cache-control"] == "no-store"
            assert missing.json() == NOT_FOUND
            assert MISSING not in missing.text

            offline = await client.get(f"/api/v1/admin/evaluations/{left.id}")
            assert offline.status_code == 200
            assert offline.json()["calibration"] is None
            assert "matrix" not in offline.text
            assert offline.json()["executed_at"] is None
            cases = await client.get(f"/api/v1/admin/evaluations/{left.id}/cases")
            assert cases.status_code == 200
            assert cases.json()["items"][0]["case_id"] == "q01-single"
            mismatched = await client.get(
                f"/api/v1/admin/evaluations/{left.id}/cases",
                params={
                    "category": "citation",
                    "cursor": encode_case_cursor("retrieval", "q01-single"),
                },
            )
            assert mismatched.status_code == 422
            assert mismatched.json()["error"]["message"] == "cursor: invalid value"

            detail = await client.get(f"/api/v1/admin/evaluations/{live.id}")
            assert detail.status_code == 200
            live_body = detail.json()
            assert live_body["calibration"]["compared"] == 13
            assert live_body["counts"]["skipped"] == 2
            assert live_body["counts"]["failed"] == 0
            labels = [
                "supported",
                "unsupported",
                "insufficient_context",
                "contradictory",
            ]
            assert live_body["calibration"]["labels"] == labels
            assert set(live_body["calibration"]["matrix"]) == set(labels)
            predicted = live_body["calibration"]["matrix"]["supported"]
            assert set(predicted) == set(labels)

            compatible = await client.get(
                "/api/v1/admin/evaluations/compare",
                params={"left_id": str(left.id), "right_id": str(right.id)},
            )
            assert compatible.status_code == 200
            compared = compatible.json()
            assert compared["comparable"] is True
            assert compared["reasons"] == []
            assert compared["warnings"] == ["execution_time_unknown"]
            assert compared["deltas"]["retrieval"]["recall_at_k"] == "0.300000"
            assert compared["deltas"]["retrieval"]["mrr_at_k"] is None
            assert compared["deltas"]["citations"]["valid"] == 2
            assert compared["deltas"]["matrix"] is None
            incompatible = await client.get(
                "/api/v1/admin/evaluations/compare",
                params={"left_id": str(left.id), "right_id": str(other.id)},
            )
            assert incompatible.status_code == 200
            blocked_pair = incompatible.json()
            assert blocked_pair["comparable"] is False
            assert blocked_pair["reasons"] == ["dataset_mismatch"]
            assert blocked_pair["deltas"] is None
            assert blocked_pair["left"]["retrieval"]["recall_at_k"] == "0.100000"
            assert blocked_pair["right"]["retrieval"]["recall_at_k"] == "0.100000"
            assert "0.300000" not in incompatible.text
            absent = await client.get(
                "/api/v1/admin/evaluations/compare",
                params={"left_id": str(left.id), "right_id": MISSING},
            )
            assert absent.status_code == 404
            assert absent.json() == NOT_FOUND
            assert MISSING not in absent.text

            for body in (
                first.text,
                offline.text,
                cases.text,
                detail.text,
                compatible.text,
                incompatible.text,
            ):
                _absent(body)

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE evaluation_case_results "
                        "SET result = result || CAST(:extra AS jsonb) "
                        "WHERE evaluation_run_id = :id"
                    ),
                    {"extra": json.dumps({"rationale": RATIONALE}), "id": left.id},
                )
            unreadable = await client.get(f"/api/v1/admin/evaluations/{left.id}/cases")
            assert unreadable.status_code == 500
            assert unreadable.json()["error"]["message"] == (
                "The evaluation could not be read."
            )
            assert RATIONALE not in unreadable.text
            assert unreadable.headers["cache-control"] == "no-store"

            token = client.cookies.get("nexus_session")
            assert token is not None
            stored = await get_session_by_hash(factory, token_hash(token, PEPPER))
            assert stored is not None
            await revoke_session(factory, stored.id, datetime.now(UTC))
            revoked = await client.get("/api/v1/admin/evaluations")
            assert revoked.status_code == 401
            assert revoked.headers["cache-control"] == "no-store"
    finally:
        await engine.dispose()
