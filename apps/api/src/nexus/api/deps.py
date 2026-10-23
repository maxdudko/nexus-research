"""Authenticated principal. Research routes do not use this yet."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from nexus.ai.errors import forbidden, unauthenticated
from nexus.core.config import Settings
from nexus.identity.cookies import SESSION_COOKIE
from nexus.identity.sessions import session_verdict, should_touch
from nexus.identity.store import get_session_by_hash, touch_session
from nexus.identity.tokens import token_hash


@dataclass(frozen=True)
class CurrentUser:
    """The account bound to the presented session."""

    id: UUID
    email: str
    role: str
    session_id: UUID


async def get_current_user(request: Request) -> CurrentUser:
    """Resolve the session cookie. Missing and unusable sessions are identical."""
    settings: Settings = request.app.state.settings
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise unauthenticated()
    try:
        digest = token_hash(token, settings.session_pepper.get_secret_value())
    except UnicodeError:
        raise unauthenticated() from None
    stored = await get_session_by_hash(request.app.state.session_factory, digest)
    if stored is None or stored.account.email is None:
        raise unauthenticated()
    now = datetime.now(UTC)
    verdict = session_verdict(
        status=stored.account.status,
        expires_at=stored.expires_at,
        last_used_at=stored.last_used_at,
        revoked_at=stored.revoked_at,
        now=now,
        idle_seconds=settings.session_idle_seconds,
    )
    if verdict != "ok":
        raise unauthenticated()
    if should_touch(stored.last_used_at, now, settings.session_touch_seconds):
        await touch_session(request.app.state.session_factory, stored.id, now)
    return CurrentUser(
        stored.account.id,
        stored.account.email,
        stored.account.role,
        stored.id,
    )


async def require_admin(
    current: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    """Allow an administrator. This does not grant access to someone else's research."""
    if current.role != "admin":
        raise forbidden()
    return current
