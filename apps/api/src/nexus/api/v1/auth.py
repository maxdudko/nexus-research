"""Login, logout, and the current account."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from nexus.ai.errors import forbidden, invalid_credentials
from nexus.api.csrf import origin_allowed
from nexus.api.deps import CurrentUser, get_current_user
from nexus.core.config import Settings
from nexus.identity.cookies import CSRF_COOKIE, SESSION_COOKIE
from nexus.identity.errors import IdentityError
from nexus.identity.passwords import needs_rehash, verify_password
from nexus.identity.store import (
    create_session,
    expires_at,
    get_user_by_email,
    revoke_session,
    set_password,
)
from nexus.identity.tokens import new_token, token_hash

router = APIRouter()


class LoginRequest(BaseModel):
    """Credentials. Extra fields are ignored and never echoed."""

    model_config = ConfigDict(extra="ignore")

    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class IdentityResponse(BaseModel):
    """The signed-in account. No verifier or session token."""

    id: UUID
    email: str
    role: Literal["user", "admin"]


class LogoutResponse(BaseModel):
    status: Literal["ok"]


def _role(value: str) -> Literal["user", "admin"]:
    if value == "admin":
        return "admin"
    return "user"


def _set_cookies(
    response: Response,
    settings: Settings,
    session_token: str,
    csrf_token: str,
) -> None:
    secure = settings.secure_cookies()
    max_age = settings.session_absolute_seconds
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_cookies(response: Response, settings: Settings) -> None:
    secure = settings.secure_cookies()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        httponly=False,
        secure=secure,
        samesite="lax",
    )


@router.post("/login", response_model=IdentityResponse)
async def login(body: LoginRequest, request: Request) -> Response:
    """Issue a session. Unknown accounts and bad passwords look the same."""
    settings: Settings = request.app.state.settings
    if not origin_allowed(request, settings):
        raise forbidden()
    now = datetime.now(UTC)
    factory = request.app.state.session_factory
    account = await get_user_by_email(factory, body.email)
    password_hash = None if account is None else account.password_hash
    email = None if account is None else account.email
    active = (
        account is not None
        and account.status == "active"
        and password_hash is not None
        and email is not None
    )
    if not active or account is None or password_hash is None or email is None:
        verify_password(body.password, request.app.state.dummy_password_hash)
        raise invalid_credentials()
    assert account is not None
    assert password_hash is not None
    assert email is not None
    if not verify_password(body.password, password_hash):
        raise invalid_credentials()
    if needs_rehash(password_hash):
        try:
            await set_password(
                factory,
                account.id,
                body.password,
                now,
                audit=False,
                revoke=False,
            )
        except IdentityError:
            pass
    session_token = new_token()
    csrf_token = new_token()
    await create_session(
        factory,
        account.id,
        token_hash(session_token, settings.session_pepper.get_secret_value()),
        now,
        expires_at(now, settings.session_absolute_seconds),
    )
    payload = IdentityResponse(id=account.id, email=email, role=_role(account.role))
    response = JSONResponse(payload.model_dump(mode="json"))
    _set_cookies(response, settings, session_token, csrf_token)
    return response


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    current: Annotated[CurrentUser, Depends(get_current_user)],
) -> Response:
    """Revoke only the presented session and clear both cookies."""
    settings: Settings = request.app.state.settings
    await revoke_session(
        request.app.state.session_factory,
        current.session_id,
        datetime.now(UTC),
    )
    response = JSONResponse({"status": "ok"})
    _clear_cookies(response, settings)
    return response


@router.get("/me", response_model=IdentityResponse)
async def me(
    current: Annotated[CurrentUser, Depends(get_current_user)],
) -> IdentityResponse:
    """Return the signed-in account."""
    return IdentityResponse(
        id=current.id,
        email=current.email,
        role=_role(current.role),
    )
