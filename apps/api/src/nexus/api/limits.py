"""Process-local request budgets. A restart clears every counter."""

import asyncio
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from nexus.ai.errors import too_many_requests
from nexus.core.config import Settings

BILLABLE_LIMIT = 10
BILLABLE_WINDOW_SECONDS = 60
MINT_LIMIT = 30
MINT_WINDOW_SECONDS = 60 * 60
LOGIN_LIMIT_MESSAGE = "Too many sign-in attempts."
REQUEST_LIMIT_MESSAGE = "Too many requests."
_SLOT_RETRY_SECONDS = 1


class UsageLimiter:
    """Sliding windows and one active run or stream per user.

    The lock guards the maps. Callers pass a clock so tests can move time.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.clock = clock or _utc_now
        self._email_limit = settings.login_max_email_failures
        self._ip_limit = settings.login_max_ip_failures
        self._login_window = settings.login_failure_window_seconds
        self._lock = asyncio.Lock()
        self._email_failures: dict[str, list[datetime]] = {}
        self._ip_failures: dict[str, list[datetime]] = {}
        self._billable: dict[UUID, list[datetime]] = {}
        self._mints: dict[UUID, list[datetime]] = {}
        self._runs: set[UUID] = set()
        self._streams: set[UUID] = set()

    async def login_retry_after(self, email: str, host: str) -> int | None:
        """Seconds until a full bucket has room. None means the attempt may proceed."""
        async with self._lock:
            now = self.clock()
            email_stamps = _remember(
                self._email_failures,
                email,
                now,
                self._login_window,
            )
            ip_stamps = _remember(
                self._ip_failures,
                host,
                now,
                self._login_window,
            )
            waits: list[int] = []
            if len(email_stamps) >= self._email_limit:
                waits.append(_retry_after(email_stamps, now, self._login_window))
            if len(ip_stamps) >= self._ip_limit:
                waits.append(_retry_after(ip_stamps, now, self._login_window))
            if not waits:
                return None
            return max(waits)

    async def record_login_failure(self, email: str, host: str) -> None:
        """Count one rejected password for the email and the client address."""
        async with self._lock:
            now = self.clock()
            _append(self._email_failures, email, now, self._login_window)
            _append(self._ip_failures, host, now, self._login_window)

    async def clear_email_failures(self, email: str) -> None:
        """A successful login forgets that email. The address bucket stays."""
        async with self._lock:
            self._email_failures.pop(email, None)

    async def charge_billable(self, user_id: UUID) -> None:
        """Count one plan, discovery, run, stream, or evidence request."""
        await self._charge(
            self._billable,
            user_id,
            BILLABLE_LIMIT,
            BILLABLE_WINDOW_SECONDS,
        )

    async def charge_mint(self, user_id: UUID) -> None:
        """Count one scope row that is about to be inserted."""
        await self._charge(
            self._mints,
            user_id,
            MINT_LIMIT,
            MINT_WINDOW_SECONDS,
        )

    async def acquire_run(self, user_id: UUID) -> None:
        """Hold the single active /run slot for this user."""
        await self._acquire(self._runs, user_id)

    async def release_run(self, user_id: UUID) -> None:
        """Return the /run slot, including after a failure."""
        await self._release(self._runs, user_id)

    async def acquire_stream(self, user_id: UUID) -> None:
        """Hold the single active /stream slot for this user."""
        await self._acquire(self._streams, user_id)

    async def release_stream(self, user_id: UUID) -> None:
        """Return the /stream slot, including when the process cap rejects it."""
        await self._release(self._streams, user_id)

    async def _charge(
        self,
        buckets: dict[UUID, list[datetime]],
        user_id: UUID,
        limit: int,
        window: int,
    ) -> None:
        async with self._lock:
            now = self.clock()
            stamps = _remember(buckets, user_id, now, window)
            if len(stamps) >= limit:
                raise too_many_requests(
                    retry_after=_retry_after(stamps, now, window),
                    message=REQUEST_LIMIT_MESSAGE,
                )
            stamps.append(now)
            buckets[user_id] = stamps

    async def _acquire(self, held: set[UUID], user_id: UUID) -> None:
        async with self._lock:
            if user_id in held:
                raise too_many_requests(retry_after=_SLOT_RETRY_SECONDS)
            held.add(user_id)

    async def _release(self, held: set[UUID], user_id: UUID) -> None:
        async with self._lock:
            held.discard(user_id)


def client_host(host: str | None) -> str:
    """The address on the API socket. A missing client shares one bucket.

    ``X-Forwarded-For`` and ``Forwarded`` are not read. The Next.js proxy does
    not authenticate a caller address, and a client-supplied value would choose
    a fresh login bucket.
    """
    if not host:
        return "unknown"
    return host


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _remember[KeyT](
    buckets: dict[KeyT, list[datetime]],
    key: KeyT,
    now: datetime,
    window: int,
) -> list[datetime]:
    stamps = _active(buckets.get(key, []), now, window)
    if stamps:
        buckets[key] = stamps
    else:
        buckets.pop(key, None)
    return stamps


def _append(
    buckets: dict[str, list[datetime]],
    key: str,
    now: datetime,
    window: int,
) -> None:
    stamps = _active(buckets.get(key, []), now, window)
    stamps.append(now)
    buckets[key] = stamps


def _active(stamps: list[datetime], now: datetime, window: int) -> list[datetime]:
    cutoff = now - timedelta(seconds=window)
    return [stamp for stamp in stamps if stamp > cutoff]


def _retry_after(stamps: list[datetime], now: datetime, window: int) -> int:
    remaining = (min(stamps) + timedelta(seconds=window) - now).total_seconds()
    return max(1, math.ceil(remaining))
