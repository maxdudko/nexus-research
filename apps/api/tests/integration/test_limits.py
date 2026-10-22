"""Live revocation and admin exposure. Providers stay stubbed."""

import asyncio
from datetime import UTC, datetime
from io import StringIO

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select
from tests.conftest import client_for, make_settings
from tests.integration.conftest import run_alembic
from tests.integration.test_research_stream_integration import _runner, parse_sse
from tests.unit.test_research_streaming import BlockingPlanner

from nexus.api.v1.research import get_runner
from nexus.identity.cli import run_cli
from nexus.identity.store import get_session_by_hash, session_is_active
from nexus.identity.tokens import token_hash
from nexus.main import create_app
from nexus.models.run import ResearchRun
from nexus.research.runner import ResearchRunner

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-1"
ORIGIN = {"origin": "http://127.0.0.1:3000"}
QUESTION = "Where did the amber ledger hide the spare key?"


async def _cli(database_url: str, argv: list[str], stdin: str) -> int:
    return await asyncio.to_thread(
        run_cli,
        argv,
        stdin=StringIO(stdin),
        stderr=StringIO(),
        settings=make_settings(database_url=database_url),
    )


async def test_logout_interrupts_a_blocked_stream_without_another_model_call(
    test_database_url: str,
) -> None:
    upgraded = await run_alembic(test_database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert (
        await _cli(
            test_database_url,
            ["create-user", "ada@example.com"],
            PASSWORD + "\n",
        )
        == 0
    )
    settings = make_settings(
        database_url=test_database_url,
        stream_heartbeat_seconds=0.05,
    )
    app = create_app(settings)
    planner = BlockingPlanner()
    runner = _runner(planner)
    holder: dict[str, object] = {}

    async def session_active() -> bool:
        session_id = holder.get("id")
        if session_id is None:
            return False
        return await session_is_active(
            app.state.session_factory,
            session_id,  # type: ignore[arg-type]
            datetime.now(UTC),
            idle_seconds=settings.session_idle_seconds,
        )

    runner.context.session_active = session_active

    async def override() -> ResearchRunner:
        return runner

    app.dependency_overrides[get_runner] = override

    async with client_for(app) as client:
        logged_in = await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert logged_in.status_code == 200
        token = client.cookies.get("nexus_session")
        csrf = client.cookies.get("nexus_csrf")
        assert token and csrf
        stored = await get_session_by_hash(
            app.state.session_factory,
            token_hash(token, settings.session_pepper.get_secret_value()),
        )
        assert stored is not None
        holder["id"] = stored.id
        stream = asyncio.create_task(
            client.post(
                "/api/v1/research/stream",
                json={"question": QUESTION},
                headers={**ORIGIN, "x-csrf-token": csrf},
            )
        )
        await asyncio.wait_for(planner.started.wait(), timeout=2)
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as other:
            logged_out = await other.post(
                "/api/v1/auth/logout",
                headers={
                    **ORIGIN,
                    "x-csrf-token": csrf,
                    "cookie": f"nexus_session={token}; nexus_csrf={csrf}",
                },
            )
        assert logged_out.status_code == 200
        response = await asyncio.wait_for(stream, timeout=2)
        async with app.state.session_factory() as db:
            row = (await db.execute(select(ResearchRun))).scalar_one()
        status = row.status
        error_code = row.error_code

    assert response.status_code == 200
    assert "research.interrupted" in response.text
    assert planner.calls == 1
    assert status == "interrupted"
    assert error_code == "interrupted"


async def test_an_admin_receives_404_for_another_users_run(
    test_database_url: str,
) -> None:
    upgraded = await run_alembic(test_database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert (
        await _cli(
            test_database_url,
            ["bootstrap-admin", "admin@example.com"],
            PASSWORD + "\n",
        )
        == 0
    )
    assert (
        await _cli(
            test_database_url,
            ["create-user", "ada@example.com"],
            PASSWORD + "\n",
        )
        == 0
    )
    app = create_app(make_settings(database_url=test_database_url))

    async def override() -> ResearchRunner:
        return _runner()

    app.dependency_overrides[get_runner] = override

    async with client_for(app) as ada, client_for(app) as admin:
        ada_login = await ada.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        admin_login = await admin.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert ada_login.status_code == admin_login.status_code == 200
        ada_csrf = ada.cookies.get("nexus_csrf")
        admin_csrf = admin.cookies.get("nexus_csrf")
        assert ada_csrf and admin_csrf
        streamed = await ada.post(
            "/api/v1/research/stream",
            json={"question": QUESTION},
            headers={**ORIGIN, "x-csrf-token": ada_csrf},
        )
        assert streamed.status_code == 200
        frames = parse_sse(streamed.text)
        completed = frames[-1][1]
        assert completed is not None
        run_id = str(completed["run_id"])
        hidden = await admin.get(
            f"/api/v1/research/runs/{run_id}",
            headers={**ORIGIN, "x-csrf-token": admin_csrf},
        )
        listing = await admin.get(
            "/api/v1/research/runs",
            headers={**ORIGIN, "x-csrf-token": admin_csrf},
        )

    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "not_found"
    assert QUESTION not in hidden.text
    assert listing.status_code == 200
    assert QUESTION not in listing.text
