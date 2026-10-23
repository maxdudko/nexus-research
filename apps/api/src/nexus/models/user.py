"""Invited accounts and revocable server-side sessions.

The quarantine row is not a person. It exists so a later ownership migration
has a non-loginable owner for pre-Alpha research. This revision does not
attach research rows to it.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from nexus.models.base import Base

QUARANTINE_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
USER_ROLES = ("user", "admin")
USER_STATUSES = ("active", "disabled", "quarantine")


class User(Base):
    """One invited account. Email is a normalized login identifier."""

    __tablename__ = "users"
    __table_args__ = (
        Index(
            "uq_users_email",
            func.lower(text("email")),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        CheckConstraint(
            "role IN ('user', 'admin')",
            name="ck_users_role",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'quarantine')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "email IS NULL OR char_length(email) BETWEEN 1 AND 254",
            name="ck_users_email_length",
        ),
        CheckConstraint(
            "status = 'quarantine' OR email IS NOT NULL",
            name="ck_users_email_required",
        ),
        CheckConstraint(
            "status <> 'active' OR password_hash IS NOT NULL",
            name="ck_users_active_hash",
        ),
        CheckConstraint(
            "status <> 'quarantine' OR "
            "(email IS NULL AND password_hash IS NULL AND role = 'user')",
            name="ck_users_quarantine",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuthSession(Base):
    """One browser session. ``token_hash`` is an HMAC, never the cookie value."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id_expires_at", "user_id", "expires_at"),
        CheckConstraint(
            "octet_length(token_hash) = 32",
            name="ck_auth_sessions_token_hash",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_auth_sessions_expires_at",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_auth_sessions_revoked_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(length=32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
