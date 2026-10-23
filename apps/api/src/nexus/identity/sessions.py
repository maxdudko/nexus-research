"""Pure session lifetime decisions. Persistence lives in the store."""

from datetime import datetime, timedelta
from typing import Literal

SessionVerdict = Literal["ok", "expired", "idle", "revoked", "inactive"]


def session_verdict(
    *,
    status: str,
    expires_at: datetime,
    last_used_at: datetime,
    revoked_at: datetime | None,
    now: datetime,
    idle_seconds: int,
) -> SessionVerdict:
    """Decide whether a stored session may authenticate this request."""
    _aware(now)
    if revoked_at is not None:
        return "revoked"
    if status != "active":
        return "inactive"
    if now >= expires_at:
        return "expired"
    if now - last_used_at >= timedelta(seconds=idle_seconds):
        return "idle"
    return "ok"


def should_touch(last_used_at: datetime, now: datetime, touch_seconds: int) -> bool:
    """Advance last use at most once per touch window. Expiry does not move."""
    _aware(now)
    _aware(last_used_at)
    return now - last_used_at >= timedelta(seconds=touch_seconds)


def _aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
