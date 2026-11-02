"""Registration, login, logout, and the current account."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from nexus.ai.errors import (
    ResearchError,
    forbidden,
    invalid_credentials,
    too_many_requests,
)
from nexus.api.csrf import origin_allowed
from nexus.api.deps import CurrentUser, get_current_user
from nexus.api.limits import LOGIN_LIMIT_MESSAGE, client_host
from nexus.core.config import Settings
from nexus.identity.cookies import CSRF_COOKIE, SESSION_COOKIE
from nexus.identity.errors import IdentityError
from nexus.identity.passwords import hash_password, needs_rehash, verify_password
from nexus.identity.store import (
    expires_at,
    get_user_by_email,
    issue_session,
    register_user,
    revoke_session,
)
from nexus.identity.tokens import csrf_token, new_token, normalize_email, token_hash

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


async def registration_guard(request: Request) -> None:
    """Check the browser origin and charge before validating credentials."""
    settings: Settings = request.app.state.settings
    if not origin_allowed(request, settings):
        raise forbidden()
    host = client_host(None if request.client is None else request.client.host)
    await request.app.state.limiter.charge_registration(host)


@router.post(
    "/register",
    response_model=IdentityResponse,
    status_code=201,
    dependencies=[Depends(registration_guard)],
)
async def register(body: LoginRequest, request: Request) -> Response:
    """Register a regular account and immediately establish a browser session."""
    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)
    pepper = settings.session_pepper.get_secret_value()
    session_token = new_token()
    try:
        account = await register_user(
            request.app.state.session_factory,
            body.email,
            body.password,
            token_hash(session_token, pepper),
            now,
            expires_at(now, settings.session_absolute_seconds),
        )
    except IdentityError as exc:
        if exc.code == "duplicate_email":
            raise ResearchError(
                "email_unavailable", 409, "This email is unavailable. Try signing in."
            ) from None
        messages = {
            "invalid_email": "Enter a valid email address.",
            "invalid_password": "Password must be between 12 and 128 characters.",
            "password_matches_email": "Password must not equal your email address.",
        }
        raise ResearchError(
            "invalid_request",
            422,
            messages.get(exc.code, "Invalid registration details."),
        ) from None
    assert account.email is not None
    payload = IdentityResponse(id=account.id, email=account.email, role="user")
    response = JSONResponse(payload.model_dump(mode="json"), status_code=201)
    _set_cookies(response, settings, session_token, csrf_token(session_token, pepper))
    return response


@router.post("/login", response_model=IdentityResponse)
async def login(body: LoginRequest, request: Request) -> Response:
    """Issue a session. Unknown accounts and bad passwords look the same."""
    settings: Settings = request.app.state.settings
    if not origin_allowed(request, settings):
        raise forbidden()
    try:
        email_key = normalize_email(body.email)
    except IdentityError:
        verify_password(body.password, request.app.state.dummy_password_hash)
        raise invalid_credentials() from None
    host = client_host(None if request.client is None else request.client.host)
    limiter = request.app.state.limiter
    retry_after = await limiter.login_retry_after(email_key, host)
    if retry_after is not None:
        raise too_many_requests(
            retry_after=retry_after,
            message=LOGIN_LIMIT_MESSAGE,
        )
    now = datetime.now(UTC)
    factory = request.app.state.session_factory
    account = await get_user_by_email(factory, email_key)
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
        await limiter.record_login_failure(email_key, host)
        raise invalid_credentials()
    assert account is not None
    assert password_hash is not None
    assert email is not None
    if not verify_password(body.password, password_hash):
        await limiter.record_login_failure(email_key, host)
        raise invalid_credentials()
    pepper = settings.session_pepper.get_secret_value()
    replacement = hash_password(body.password) if needs_rehash(password_hash) else None
    session_token = new_token()
    issued = await issue_session(
        factory,
        account.id,
        password_hash,
        token_hash(session_token, pepper),
        now,
        expires_at(now, settings.session_absolute_seconds),
        replacement_password_hash=replacement,
    )
    if issued is None:
        raise invalid_credentials()
    csrf_value = csrf_token(session_token, pepper)
    await limiter.clear_email_failures(email_key)
    payload = IdentityResponse(id=account.id, email=email, role=_role(account.role))
    response = JSONResponse(payload.model_dump(mode="json"))
    _set_cookies(response, settings, session_token, csrf_value)
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
