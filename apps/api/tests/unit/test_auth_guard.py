"""CSRF and admin dependency before research routes are authenticated."""

from typing import Annotated
from uuid import UUID

import pytest
from fastapi import APIRouter, Depends
from tests.conftest import client_for, make_settings

from nexus.api.deps import CurrentUser, get_current_user, require_admin
from nexus.api.v1.research import get_planner
from nexus.main import create_app

QUESTION = "How do AI coding agents improve software development workflows?"
ORIGIN = "http://127.0.0.1:3000"


def _user(role: str) -> CurrentUser:
    return CurrentUser(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        email="ada@example.com",
        role=role,
        session_id=UUID("22222222-2222-4222-8222-222222222222"),
    )


async def test_login_without_origin_is_forbidden() -> None:
    app = create_app(make_settings())
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": "correct-horse-1"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "set-cookie" not in response.headers


async def test_session_cookie_without_csrf_does_not_reach_plan() -> None:
    app = create_app(make_settings())
    called = False

    def planner() -> object:
        nonlocal called
        called = True
        raise AssertionError("planner ran")

    app.dependency_overrides[get_planner] = planner
    async with client_for(app) as client:
        blocked = await client.post(
            "/api/v1/research/plan",
            json={"question": QUESTION},
            headers={"origin": ORIGIN, "cookie": "nexus_session=not-a-real-session"},
        )
    assert blocked.status_code == 403
    assert called is False
    app.dependency_overrides.pop(get_planner)
    async with client_for(app) as client:
        anonymous = await client.post(
            "/api/v1/research/plan",
            json={"question": "short"},
        )
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize("role,status_code", [("user", 403), ("admin", 200)])
async def test_require_admin_is_explicit(role: str, status_code: int) -> None:
    app = create_app(make_settings())
    probe = APIRouter()

    @probe.get("/admin-probe")
    async def admin_probe(
        current: Annotated[CurrentUser, Depends(require_admin)],
    ) -> dict[str, str]:
        return {"role": current.role}

    app.include_router(probe)
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    async with client_for(app) as client:
        response = await client.get("/admin-probe")
    assert response.status_code == status_code
    if status_code == 403:
        assert response.json() == {
            "error": {
                "code": "forbidden",
                "message": "The request was rejected.",
            }
        }
    else:
        assert response.json() == {"role": "admin"}
