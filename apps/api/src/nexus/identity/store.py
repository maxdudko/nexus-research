"""Short transactions for accounts and sessions."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.identity.errors import IdentityError
from nexus.identity.passwords import hash_password, validate_password
from nexus.identity.sessions import session_verdict
from nexus.identity.tokens import normalize_email
from nexus.models.user import QUARANTINE_USER_ID, AuthSession, User
from nexus.observability.logging import log_event

USER_ROLES = frozenset({"user", "admin"})


class Account:
    """A user row detached from the session that loaded it."""

    def __init__(
        self,
        user_id: uuid.UUID,
        email: str | None,
        role: str,
        status: str,
        password_hash: str | None,
    ) -> None:
        self.id = user_id
        self.email = email
        self.role = role
        self.status = status
        self.password_hash = password_hash


class StoredSession:
    """A session joined to its account, detached from the database session."""

    def __init__(
        self,
        session_id: uuid.UUID,
        account: Account,
        expires_at: datetime,
        last_used_at: datetime,
        revoked_at: datetime | None,
    ) -> None:
        self.id = session_id
        self.account = account
        self.expires_at = expires_at
        self.last_used_at = last_used_at
        self.revoked_at = revoked_at


def _account(user: User) -> Account:
    return Account(
        user.id,
        user.email,
        user.role,
        user.status,
        user.password_hash,
    )


def _audit(action: str, user_id: uuid.UUID) -> None:
    log_event("identity.audit", action=action, target_user_id=str(user_id))


async def any_admin(factory: async_sessionmaker[AsyncSession]) -> bool:
    """Return whether an administrator account already exists."""
    async with factory() as db, db.begin():
        found = await db.scalar(select(User.id).where(User.role == "admin").limit(1))
    return found is not None


async def bootstrap_admin(
    factory: async_sessionmaker[AsyncSession],
    email: str,
    password: str,
    now: datetime,
) -> uuid.UUID | None:
    """Create the first admin. Return None when one already exists."""
    normalized = normalize_email(email)
    validate_password(password, normalized)
    password_hash = hash_password(password)
    user_id = uuid.uuid4()
    try:
        async with factory() as db, db.begin():
            await db.scalar(
                select(User).where(User.id == QUARANTINE_USER_ID).with_for_update()
            )
            existing = await db.scalar(
                select(User.id).where(User.role == "admin").limit(1)
            )
            if existing is not None:
                return None
            db.add(
                User(
                    id=user_id,
                    email=normalized,
                    role="admin",
                    status="active",
                    password_hash=password_hash,
                    created_at=now,
                    updated_at=now,
                )
            )
    except IntegrityError:
        raise IdentityError("duplicate_email") from None
    _audit("bootstrap_admin", user_id)
    return user_id


async def create_user(
    factory: async_sessionmaker[AsyncSession],
    email: str,
    password: str,
    *,
    role: str,
    now: datetime,
) -> uuid.UUID:
    """Insert an active account. Duplicate email is a fixed error."""
    if role not in USER_ROLES:
        raise IdentityError("invalid_role")
    normalized = normalize_email(email)
    validate_password(password, normalized)
    password_hash = hash_password(password)
    user_id = uuid.uuid4()
    try:
        async with factory() as db, db.begin():
            db.add(
                User(
                    id=user_id,
                    email=normalized,
                    role=role,
                    status="active",
                    password_hash=password_hash,
                    created_at=now,
                    updated_at=now,
                )
            )
    except IntegrityError:
        raise IdentityError("duplicate_email") from None
    _audit("create_user", user_id)
    return user_id


async def get_user_by_email(
    factory: async_sessionmaker[AsyncSession],
    email: str,
) -> Account | None:
    """Load one account by its normalized email."""
    try:
        normalized = normalize_email(email)
    except IdentityError:
        return None
    async with factory() as db, db.begin():
        user = await db.scalar(select(User).where(User.email == normalized))
        if user is None:
            return None
        return _account(user)


async def set_password(
    factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    password: str,
    now: datetime,
    *,
    audit: bool,
    revoke: bool = True,
) -> None:
    """Replace a verifier. Quarantine accounts cannot be changed."""
    async with factory() as db, db.begin():
        user = await _lock_mutable(db, user_id)
        if user.email is None:
            raise IdentityError("quarantine")
        validate_password(password, user.email)
        user.password_hash = hash_password(password)
        user.updated_at = now
        if revoke:
            await _revoke_all(db, user_id, now)
    if audit:
        _audit("reset_password", user_id)


async def disable_user(
    factory: async_sessionmaker[AsyncSession],
    email: str,
    now: datetime,
) -> uuid.UUID:
    """Disable an account and revoke its sessions."""
    normalized = normalize_email(email)
    async with factory() as db, db.begin():
        user = await db.scalar(
            select(User).where(User.email == normalized).with_for_update()
        )
        if user is None:
            raise IdentityError("not_found")
        if user.status == "quarantine" or user.id == QUARANTINE_USER_ID:
            raise IdentityError("quarantine")
        user.status = "disabled"
        user.updated_at = now
        await _revoke_all(db, user.id, now)
        user_id = user.id
    _audit("disable_user", user_id)
    return user_id


async def create_session(
    factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    token_hash: bytes,
    now: datetime,
    expires_at: datetime,
) -> uuid.UUID:
    """Store a session digest. The raw token is not a column."""
    session_id = uuid.uuid4()
    async with factory() as db, db.begin():
        db.add(
            AuthSession(
                id=session_id,
                user_id=user_id,
                token_hash=token_hash,
                created_at=now,
                last_used_at=now,
                expires_at=expires_at,
                revoked_at=None,
            )
        )
    return session_id


async def get_session_by_hash(
    factory: async_sessionmaker[AsyncSession],
    token_hash: bytes,
) -> StoredSession | None:
    """Load a session and its account by HMAC digest."""
    async with factory() as db, db.begin():
        row = await db.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == token_hash)
        )
        found = row.one_or_none()
        if found is None:
            return None
        auth_session, user = found
        return StoredSession(
            auth_session.id,
            _account(user),
            auth_session.expires_at,
            auth_session.last_used_at,
            auth_session.revoked_at,
        )


async def session_is_active(
    factory: async_sessionmaker[AsyncSession],
    session_id: uuid.UUID,
    now: datetime,
    *,
    idle_seconds: int,
) -> bool:
    """True when this session is unrevoked, inside both lifetimes, and active."""
    async with factory() as db, db.begin():
        row = await db.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.id == session_id)
        )
        found = row.one_or_none()
    if found is None:
        return False
    auth_session, user = found
    return (
        session_verdict(
            status=user.status,
            expires_at=auth_session.expires_at,
            last_used_at=auth_session.last_used_at,
            revoked_at=auth_session.revoked_at,
            now=now,
            idle_seconds=idle_seconds,
        )
        == "ok"
    )


async def touch_session(
    factory: async_sessionmaker[AsyncSession],
    session_id: uuid.UUID,
    now: datetime,
) -> None:
    """Move last use forward. Absolute expiry stays where it was."""
    async with factory() as db, db.begin():
        auth_session = await db.get(AuthSession, session_id)
        if auth_session is None:
            return
        auth_session.last_used_at = now


async def revoke_session(
    factory: async_sessionmaker[AsyncSession],
    session_id: uuid.UUID,
    now: datetime,
) -> None:
    """Revoke one session. A missing row is not an error."""
    async with factory() as db, db.begin():
        auth_session = await db.get(AuthSession, session_id)
        if auth_session is None or auth_session.revoked_at is not None:
            return
        auth_session.revoked_at = now


async def session_count(
    factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
) -> int:
    """Count sessions for tests and operator checks."""
    async with factory() as db, db.begin():
        rows = await db.scalars(
            select(AuthSession.id).where(AuthSession.user_id == user_id)
        )
    return len(list(rows))


def expires_at(now: datetime, absolute_seconds: int) -> datetime:
    """Absolute end of a new session. Later requests do not extend it."""
    return now + timedelta(seconds=absolute_seconds)


async def _lock_mutable(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise IdentityError("not_found")
    if user.status == "quarantine" or user.id == QUARANTINE_USER_ID:
        raise IdentityError("quarantine")
    return user


async def _revoke_all(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
