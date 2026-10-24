"""Diagnostics and usage against PostgreSQL. No provider client is constructed."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
from sqlalchemy import text
from tests.conftest import client_for, make_settings
from tests.integration.conftest import run_alembic

from nexus.identity.cli import run_cli
from nexus.infrastructure.database import create_db_engine
from nexus.main import create_app
from nexus.models.user import QUARANTINE_USER_ID

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-1"
PEPPER = "integration-test-pepper-0123456789abcd"
ORIGIN = {"origin": "http://127.0.0.1:3000"}
ADMIN = "canary-admin@example.com"
USER = "canary-user@example.com"
QUESTION = "Canary question about private research sources."
REPORT = "Canary report sentence stays private."
SOURCE_NAME = "Canary source display name stays private."
USAGE_NOTE = "CANARY_USAGE_TOKENS"
LIVE_RATIONALE = "The excerpts entail the planted claim."
ROUTES = (
    "/api/v1/admin/overview",
    "/api/v1/admin/diagnostics",
    "/api/v1/admin/diagnostics/runs",
    "/api/v1/admin/usage",
)


def _settings(database_url: str):
    return make_settings(
        environment="test",
        database_url=database_url,
        session_pepper=PEPPER,
    )


async def _cli(database_url: str, argv: list[str]) -> None:
    code = await asyncio.to_thread(
        run_cli,
        argv,
        stdin=StringIO(PASSWORD + "\n"),
        stderr=StringIO(),
        settings=_settings(database_url),
    )
    assert code == 0


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
    assert SOURCE_NAME not in body
    assert USAGE_NOTE not in body
    assert LIVE_RATIONALE not in body
    assert ADMIN not in body
    assert USER not in body


async def _seed(database_url: str, moment: datetime) -> dict[str, str]:
    engine = create_db_engine(database_url)
    research_id = uuid.uuid4()
    ids = {
        "stale": str(uuid.uuid4()),
        "unstarted": str(uuid.uuid4()),
        "completed": str(uuid.uuid4()),
        "partial": str(uuid.uuid4()),
        "insufficient": str(uuid.uuid4()),
        "failed": str(uuid.uuid4()),
        "interrupted": str(uuid.uuid4()),
    }
    valid_usage = {
        "iterations": 2,
        "search_calls": 1,
        "llm_calls": 4,
        "pages_indexed": 3,
        "pages_failed": 1,
    }
    rows = [
        (
            "stale",
            "running",
            None,
            moment - timedelta(hours=1),
            moment - timedelta(minutes=30),
            None,
            None,
        ),
        ("unstarted", "running", None, moment - timedelta(hours=2), None, None, None),
        (
            "completed",
            "completed",
            None,
            moment - timedelta(hours=3),
            moment - timedelta(hours=3),
            moment - timedelta(hours=3) + timedelta(milliseconds=1500),
            valid_usage,
        ),
        (
            "partial",
            "partial",
            None,
            moment - timedelta(hours=4),
            moment - timedelta(hours=4),
            moment - timedelta(hours=4) - timedelta(seconds=5),
            {"note": USAGE_NOTE, "rationale": LIVE_RATIONALE},
        ),
        (
            "insufficient",
            "insufficient_evidence",
            None,
            moment - timedelta(hours=5),
            None,
            moment - timedelta(hours=5),
            None,
        ),
        (
            "failed",
            "failed",
            "provider_unavailable",
            moment - timedelta(hours=6),
            moment - timedelta(hours=6),
            moment - timedelta(hours=6) + timedelta(milliseconds=2500),
            None,
        ),
        (
            "interrupted",
            "interrupted",
            "client_disconnected",
            moment - timedelta(hours=7),
            None,
            moment - timedelta(hours=7),
            None,
        ),
    ]
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
                    "INSERT INTO sources "
                    "(id, research_id, type, uri, display_name, status, chunk_count) "
                    "VALUES (:id, :research_id, 'WEB', :uri, :name, 'PENDING', 0)"
                ),
                {
                    "id": uuid.uuid4(),
                    "research_id": research_id,
                    "uri": "https://example.test/canary",
                    "name": SOURCE_NAME,
                },
            )
            for (
                key,
                status,
                error_code,
                created_at,
                started_at,
                finished_at,
                usage,
            ) in rows:
                await connection.execute(
                    text(
                        "INSERT INTO research_runs ("
                        "run_id, research_id, question, status, error_code, "
                        "created_at, started_at, finished_at, updated_at, usage"
                        ") VALUES ("
                        ":run_id, :research_id, :question, :status, :error_code, "
                        ":created_at, :started_at, :finished_at, :updated_at, "
                        "CAST(:usage AS jsonb))"
                    ),
                    {
                        "run_id": ids[key],
                        "research_id": research_id,
                        "question": QUESTION,
                        "status": status,
                        "error_code": error_code,
                        "created_at": created_at,
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "updated_at": moment,
                        "usage": None if usage is None else json.dumps(usage),
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO research_reports (run_id, report) "
                    "VALUES (:run_id, CAST(:report AS jsonb))"
                ),
                {
                    "run_id": ids["completed"],
                    "report": json.dumps(
                        {"sentence": REPORT, "rationale": LIVE_RATIONALE}
                    ),
                },
            )
            plan = await connection.execute(
                text(
                    "EXPLAIN SELECT run_id, status, error_code, created_at, "
                    "started_at, finished_at FROM research_runs "
                    "WHERE created_at >= :start AND created_at < :end "
                    "ORDER BY created_at DESC, run_id DESC LIMIT 21"
                ),
                {
                    "start": moment - timedelta(days=7),
                    "end": moment + timedelta(minutes=1),
                },
            )
            explained = "\n".join(row[0] for row in plan)
    finally:
        await engine.dispose()
    assert "created_at" in explained
    assert "run_id" in explained
    return ids


async def test_diagnostics_and_usage_stay_inside_the_technical_columns(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = await run_alembic(test_database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr
    await _cli(test_database_url, ["bootstrap-admin", ADMIN])
    await _cli(test_database_url, ["create-user", USER])
    moment = datetime.now(UTC)
    ids = await _seed(test_database_url, moment)
    constructed: list[str] = []

    def reject_qdrant(*args: object, **kwargs: object) -> None:
        del args, kwargs
        constructed.append("qdrant")
        raise AssertionError("qdrant")

    def reject_chat(*args: object, **kwargs: object) -> None:
        del args, kwargs
        constructed.append("chat")
        raise AssertionError("chat")

    monkeypatch.setattr(
        "nexus.retrieval.vector_store.create_qdrant_client",
        reject_qdrant,
    )
    monkeypatch.setattr("nexus.ai.client.build_chat_model", reject_chat)
    app = create_app(_settings(test_database_url))
    window = {
        "from": (moment - timedelta(days=2)).isoformat(),
        "to": (moment + timedelta(minutes=1)).isoformat(),
    }
    async with client_for(app) as client:
        for path in ROUTES:
            anonymous = await client.get(path)
            assert anonymous.status_code == 401
            assert anonymous.headers["cache-control"] == "no-store"
        await _login(client, USER)
        for path in ROUTES:
            rejected = await client.get(path)
            assert rejected.status_code == 403
            assert rejected.headers["cache-control"] == "no-store"
            _absent(rejected.text)
        client.cookies.clear()
        await _login(client, ADMIN)
        hidden = await client.get(f"/api/v1/research/runs/{ids['completed']}")
        assert hidden.status_code == 404
        _absent(hidden.text)

        diagnostics = await client.get("/api/v1/admin/diagnostics", params=window)
        assert diagnostics.status_code == 200
        assert diagnostics.headers["cache-control"] == "no-store"
        _absent(diagnostics.text)
        body = diagnostics.json()
        assert body["statuses"] == {
            "running": 2,
            "completed": 1,
            "partial": 1,
            "insufficient_evidence": 1,
            "failed": 1,
            "interrupted": 1,
            "recorded_success": 3,
            "recorded_failure": 2,
            "recorded_running": 2,
        }
        assert body["stale_running"] == 1
        assert body["running_without_start"] == 1
        assert body["errors"] == [
            {"error_code": "client_disconnected", "count": 1},
            {"error_code": "provider_unavailable", "count": 1},
        ]
        assert body["duration"] == {
            "known_count": 2,
            "unknown_count": 5,
            "total_seconds": "4.000",
            "max_seconds": "2.500",
        }

        empty = await client.get(
            "/api/v1/admin/diagnostics",
            params={
                "from": (moment + timedelta(days=2)).isoformat(),
                "to": (moment + timedelta(days=3)).isoformat(),
            },
        )
        assert empty.status_code == 200
        empty_body = empty.json()
        assert empty_body["statuses"]["running"] == 0
        assert empty_body["statuses"]["recorded_success"] == 0
        assert empty_body["stale_running"] == 0
        assert empty_body["duration"]["known_count"] == 0
        assert empty_body["duration"]["total_seconds"] is None
        assert empty_body["duration"]["max_seconds"] is None

        too_wide = await client.get(
            "/api/v1/admin/diagnostics",
            params={
                "from": (moment - timedelta(days=91)).isoformat(),
                "to": moment.isoformat(),
            },
        )
        assert too_wide.status_code == 422
        assert too_wide.json()["error"]["message"] == "window: invalid value"
        naive = await client.get(
            "/api/v1/admin/diagnostics",
            params={"from": "2026-10-01T00:00:00", "to": moment.isoformat()},
        )
        assert naive.status_code == 422
        assert naive.json()["error"]["message"] == "from: invalid value"

        first = await client.get(
            "/api/v1/admin/diagnostics/runs",
            params={**window, "limit": "2"},
        )
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        _absent(first.text)
        page = first.json()
        assert [item["run_id"] for item in page["items"]] == [
            ids["stale"],
            ids["unstarted"],
        ]
        assert page["items"][0]["duration"] is None
        assert page["next_cursor"]
        repeated = await client.get(
            "/api/v1/admin/diagnostics/runs",
            params={**window, "limit": "2"},
        )
        assert [item["run_id"] for item in repeated.json()["items"]] == [
            ids["stale"],
            ids["unstarted"],
        ]
        second = await client.get(
            "/api/v1/admin/diagnostics/runs",
            params={**window, "limit": "2", "cursor": page["next_cursor"]},
        )
        assert second.status_code == 200
        assert [item["run_id"] for item in second.json()["items"]] == [
            ids["completed"],
            ids["partial"],
        ]
        completed_row = second.json()["items"][0]
        partial_row = second.json()["items"][1]
        assert completed_row["duration"] == "1.500"
        assert partial_row["duration"] is None
        assert "question" not in completed_row
        bad_cursor = await client.get(
            "/api/v1/admin/diagnostics/runs",
            params={"cursor": "not-a-cursor"},
        )
        assert bad_cursor.status_code == 422
        assert bad_cursor.json()["error"]["message"] == "cursor: invalid value"
        bad_limit = await client.get(
            "/api/v1/admin/diagnostics/runs",
            params={"limit": "51"},
        )
        assert bad_limit.status_code == 422
        assert bad_limit.json()["error"]["message"] == "limit: invalid value"

        usage = await client.get("/api/v1/admin/usage", params=window)
        assert usage.status_code == 200
        assert usage.headers["cache-control"] == "no-store"
        _absent(usage.text)
        usage_body = usage.json()
        assert usage_body["runs_with_usage"] == 1
        assert usage_body["runs_without_usage"] == 6
        assert usage_body["counters"]["llm_calls"] == 4
        assert usage_body["counters"]["iterations"] == 2
        assert usage_body["cost_estimate"] is None
        assert usage_body["coverage"] == "unavailable"
        assert usage_body["pricing_version"] is None
        empty_usage = await client.get(
            "/api/v1/admin/usage",
            params={
                "from": (moment + timedelta(days=2)).isoformat(),
                "to": (moment + timedelta(days=3)).isoformat(),
            },
        )
        assert empty_usage.json()["cost_estimate"] is None
        assert empty_usage.json()["counters"]["llm_calls"] == 0
        assert empty_usage.json()["runs_without_usage"] == 0

        overview = await client.get("/api/v1/admin/overview")
        assert overview.status_code == 200
        assert overview.headers["cache-control"] == "no-store"
        _absent(overview.text)
        assert overview.json()["latest_evaluation"] is None
        assert overview.json()["diagnostics"]["stale_running"] == 1
    assert constructed == []
