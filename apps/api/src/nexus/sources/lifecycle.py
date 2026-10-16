"""Claim and finish a source without committing the session.

Callers own the transaction. These functions flush so constraint failures
surface before commit, then leave the transaction open.

A PDF row is one digest. The same extracted text claims the same row, and a
different digest inserts another row. A web row is one URL. A new digest
updates that row, and ``content_hash`` remains the last accepted text.

Group B indexes one attempt in this order:

1. ``claim_source``, then commit.
2. Embed outside the transaction.
3. Reload the row. When the token was replaced, call ``abandon_claim`` and stop.
4. Upsert points tagged with this claim token.
5. Reload the row again. When the token was replaced, call ``abandon_claim``
   and stop.
6. ``retire_other_claims`` and ``finish_indexed`` in one transaction, then
   commit. Only after that commit deletes other accepted tokens.

Embedding and the upsert stay outside the row lock. ``retire_other_claims``
holds that lock while it marks this token accepted. It does not delete.
The previous accepted points stay until the commit succeeds, so a rollback
still has them. The delete after commit matches ``accepted=true`` and skips
this token, so an attempt that started after the lock was released is kept.
A late upsert uses a different point id and stays unaccepted until its own
retire.

A later claim replaces ``indexing_token``. ``finish_*`` and
``retire_other_claims`` then raise ``StaleClaim`` and do not write. A stale
attempt deletes only points tagged with its own token.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.models.source import (
    CONTENT_HASH,
    DISPLAY_NAME_MAX_LENGTH,
    ERROR_CODE,
    PDF_URI_PREFIX,
    URI_MAX_LENGTH,
    Source,
    SourceStatus,
    SourceType,
)


class ClaimAction(StrEnum):
    """Whether the caller should embed this attempt."""

    CLAIMED = "claimed"
    DEDUPLICATED = "deduplicated"


class StaleClaim(Exception):
    """This indexing token no longer owns the source."""

    def __init__(self) -> None:
        super().__init__("The indexing token no longer owns this source.")


class ClaimPoints(Protocol):
    """The Qdrant deletes Group B uses while a claim token is in play."""

    async def delete_claim(
        self,
        *,
        research_id: uuid.UUID,
        source_id: uuid.UUID,
        claim_token: uuid.UUID,
    ) -> None: ...

    async def delete_other_claims(
        self,
        *,
        research_id: uuid.UUID,
        source_id: uuid.UUID,
        claim_token: uuid.UUID,
    ) -> None: ...

    async def accept_claim(
        self,
        *,
        research_id: uuid.UUID,
        source_id: uuid.UUID,
        claim_token: uuid.UUID,
    ) -> None: ...


@dataclass(frozen=True)
class ClaimRequest:
    """Identity and attempt hash after validation."""

    research_id: uuid.UUID
    source_type: SourceType
    uri: str
    content_hash: str | None
    display_name: str | None


@dataclass(frozen=True)
class SourceClaim:
    """The row a caller may finish, or an unchanged indexed source."""

    source_id: uuid.UUID
    research_id: uuid.UUID
    action: ClaimAction
    token: uuid.UUID | None
    status: SourceStatus
    content_hash: str | None
    indexing_content_hash: str | None
    uri: str


def normalize_claim(
    *,
    research_id: uuid.UUID,
    source_type: SourceType,
    uri: str | None,
    content_hash: str | None,
    display_name: str | None,
) -> ClaimRequest:
    """Reject identities the table constraints would also reject."""
    if not isinstance(research_id, uuid.UUID):
        raise ValueError("research_id must be a UUID")
    if content_hash is not None:
        content_hash = _require_hash(content_hash)
    name = _normalize_display_name(display_name)
    if source_type is SourceType.PDF:
        if content_hash is None:
            raise ValueError("content_hash is required for a PDF source")
        expected_uri = f"{PDF_URI_PREFIX}{content_hash}"
        if uri not in (None, expected_uri):
            raise ValueError("PDF uri must be pdf:{content_hash}")
        return ClaimRequest(
            research_id=research_id,
            source_type=source_type,
            uri=expected_uri,
            content_hash=content_hash,
            display_name=name,
        )
    if source_type is not SourceType.WEB:
        raise ValueError("source type is not supported")
    if uri is None:
        raise ValueError("uri is required for a web source")
    stripped = uri.strip()
    if not stripped:
        raise ValueError("uri must not be empty")
    if len(stripped) > URI_MAX_LENGTH:
        raise ValueError("uri must be at most 2048 characters")
    if stripped.startswith(PDF_URI_PREFIX):
        raise ValueError("web uri must not use the pdf: prefix")
    return ClaimRequest(
        research_id=research_id,
        source_type=SourceType.WEB,
        uri=stripped,
        content_hash=content_hash,
        display_name=name,
    )


def plan_claim(
    *,
    status: SourceStatus | None,
    content_hash: str | None,
    incoming_hash: str | None,
) -> ClaimAction:
    """Decide whether an existing row needs another indexing attempt.

    ``status is None`` means the row does not exist yet. Only an INDEXED row
    whose stored digest equals the incoming digest is reusable. PENDING,
    INDEXING, FAILED, a different digest, and a missing digest all take the
    claim and replace the token.
    """
    if (
        status is SourceStatus.INDEXED
        and incoming_hash is not None
        and incoming_hash == content_hash
    ):
        return ClaimAction.DEDUPLICATED
    return ClaimAction.CLAIMED


def plan_finish_indexed(
    *,
    status: SourceStatus,
    owner_token: uuid.UUID | None,
    token: uuid.UUID,
    indexing_content_hash: str | None,
    content_hash: str,
    chunk_count: int,
) -> str:
    """Return the digest that may become the last accepted hash."""
    _require_owner(status, owner_token, token)
    digest = _require_hash(content_hash)
    if indexing_content_hash is not None and indexing_content_hash != digest:
        raise ValueError("content_hash does not match the claim")
    if chunk_count < 0:
        raise ValueError("chunk_count must be non-negative")
    return digest


def plan_finish_failed(
    *,
    status: SourceStatus,
    owner_token: uuid.UUID | None,
    token: uuid.UUID,
    error_code: str,
) -> str:
    """Return the public error token for a failed attempt."""
    _require_owner(status, owner_token, token)
    if ERROR_CODE.fullmatch(error_code) is None:
        raise ValueError("error_code must be a short lowercase token")
    return error_code


def plan_finish_unchanged(
    *,
    status: SourceStatus,
    owner_token: uuid.UUID | None,
    token: uuid.UUID,
    content_hash: str | None,
    fetched_hash: str,
) -> None:
    """Allow a web refresh to return to INDEXED without another embed."""
    _require_owner(status, owner_token, token)
    fetched = _require_hash(fetched_hash)
    if content_hash is None or content_hash != fetched:
        raise ValueError("fetched hash does not match the indexed content")


async def claim_source(
    session: AsyncSession,
    *,
    research_id: uuid.UUID,
    source_type: SourceType,
    uri: str | None = None,
    content_hash: str | None = None,
    display_name: str | None = None,
) -> SourceClaim:
    """Lock the identity row, or insert it, and either claim or deduplicate.

    Does not commit. PDF rows store ``content_hash`` immediately because it is
    their identity. Web rows leave ``content_hash`` empty until
    ``finish_indexed``; the attempt digest is ``indexing_content_hash``.
    """
    request = normalize_claim(
        research_id=research_id,
        source_type=source_type,
        uri=uri,
        content_hash=content_hash,
        display_name=display_name,
    )
    existing = await _lock_identity(session, request)
    if existing is None:
        inserted = await _insert_claim(session, request)
        if inserted is not None:
            return inserted
        existing = await _lock_identity(session, request)
        if existing is None:
            raise RuntimeError("source claim lost the identity row")
    return await _reclaim(session, existing, request)


async def finish_indexed(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    token: uuid.UUID,
    content_hash: str,
    chunk_count: int,
) -> None:
    """Mark the attempt indexed. A replaced token writes nothing."""
    source = await _lock_id(session, source_id)
    digest = plan_finish_indexed(
        status=source.status,
        owner_token=source.indexing_token,
        token=token,
        indexing_content_hash=source.indexing_content_hash,
        content_hash=content_hash,
        chunk_count=chunk_count,
    )
    if source.type is SourceType.PDF and source.content_hash not in (None, digest):
        raise ValueError("PDF content_hash is the source identity")
    source.content_hash = digest
    source.chunk_count = chunk_count
    source.status = SourceStatus.INDEXED
    source.indexing_token = None
    source.indexing_content_hash = None
    source.error_code = None
    await session.flush()


async def finish_failed(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    token: uuid.UUID,
    error_code: str,
) -> None:
    """Record a failed attempt and keep the last accepted digest."""
    source = await _lock_id(session, source_id)
    code = plan_finish_failed(
        status=source.status,
        owner_token=source.indexing_token,
        token=token,
        error_code=error_code,
    )
    source.status = SourceStatus.FAILED
    source.error_code = code
    source.indexing_token = None
    source.indexing_content_hash = None
    await session.flush()


async def finish_unchanged(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    token: uuid.UUID,
    content_hash: str,
) -> None:
    """Return an in-progress row to INDEXED when the digest did not change."""
    source = await _lock_id(session, source_id)
    plan_finish_unchanged(
        status=source.status,
        owner_token=source.indexing_token,
        token=token,
        content_hash=source.content_hash,
        fetched_hash=content_hash,
    )
    source.status = SourceStatus.INDEXED
    source.indexing_token = None
    source.indexing_content_hash = None
    source.error_code = None
    await session.flush()


async def retire_other_claims(
    session: AsyncSession,
    store: ClaimPoints,
    *,
    source_id: uuid.UUID,
    token: uuid.UUID,
) -> None:
    """Accept this token's points while the row lock is held.

    The caller commits afterward, together with ``finish_indexed``, and only
    then deletes other accepted tokens. A replaced token raises ``StaleClaim``
    before the accept.
    """
    source = await _lock_id(session, source_id)
    _require_owner(source.status, source.indexing_token, token)
    await store.accept_claim(
        research_id=source.research_id,
        source_id=source.id,
        claim_token=token,
    )


async def abandon_claim(
    store: ClaimPoints,
    *,
    research_id: uuid.UUID,
    source_id: uuid.UUID,
    token: uuid.UUID,
) -> None:
    """Delete points tagged with this token. A newer token is left in place."""
    await store.delete_claim(
        research_id=research_id,
        source_id=source_id,
        claim_token=token,
    )


def _require_owner(
    status: SourceStatus,
    owner_token: uuid.UUID | None,
    token: uuid.UUID,
) -> None:
    if status is not SourceStatus.INDEXING or owner_token != token:
        raise StaleClaim()


def _require_hash(value: str) -> str:
    if CONTENT_HASH.fullmatch(value) is None:
        raise ValueError("content_hash must be 64 lowercase hex characters")
    return value


def _normalize_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > DISPLAY_NAME_MAX_LENGTH:
        raise ValueError("display_name must be at most 300 characters")
    return stripped


def _as_claim(
    source: Source,
    action: ClaimAction,
    token: uuid.UUID | None,
) -> SourceClaim:
    return SourceClaim(
        source_id=source.id,
        research_id=source.research_id,
        action=action,
        token=token,
        status=source.status,
        content_hash=source.content_hash,
        indexing_content_hash=source.indexing_content_hash,
        uri=source.uri,
    )


async def _lock_identity(session: AsyncSession, request: ClaimRequest) -> Source | None:
    statement = select(Source).where(
        Source.research_id == request.research_id,
        Source.type == request.source_type,
    )
    if request.source_type is SourceType.PDF:
        statement = statement.where(Source.content_hash == request.content_hash)
    else:
        statement = statement.where(Source.uri == request.uri)
    result = await session.execute(statement.with_for_update())
    return result.scalar_one_or_none()


def _stored_hash(request: ClaimRequest) -> str | None:
    """PDF identity is stored immediately. A web digest waits for success."""
    if request.source_type is SourceType.WEB:
        return None
    return request.content_hash


async def _insert_claim(
    session: AsyncSession,
    request: ClaimRequest,
) -> SourceClaim | None:
    token = uuid.uuid4()
    source = Source(
        research_id=request.research_id,
        type=request.source_type,
        uri=request.uri,
        display_name=request.display_name,
        content_hash=_stored_hash(request),
        indexing_content_hash=request.content_hash,
        indexing_token=token,
        status=SourceStatus.INDEXING,
        error_code=None,
        chunk_count=0,
    )
    try:
        async with session.begin_nested():
            session.add(source)
            await session.flush()
    except IntegrityError:
        return None
    return _as_claim(source, ClaimAction.CLAIMED, token)


async def _reclaim(
    session: AsyncSession,
    source: Source,
    request: ClaimRequest,
) -> SourceClaim:
    action = plan_claim(
        status=source.status,
        content_hash=source.content_hash,
        incoming_hash=request.content_hash,
    )
    if action is ClaimAction.DEDUPLICATED:
        return _as_claim(source, action, None)
    token = uuid.uuid4()
    source.status = SourceStatus.INDEXING
    source.indexing_token = token
    source.error_code = None
    source.indexing_content_hash = request.content_hash
    if request.display_name is not None:
        source.display_name = request.display_name
    await session.flush()
    return _as_claim(source, ClaimAction.CLAIMED, token)


async def _lock_id(session: AsyncSession, source_id: uuid.UUID) -> Source:
    statement = select(Source).where(Source.id == source_id).with_for_update()
    source = (await session.execute(statement)).scalar_one_or_none()
    if source is None:
        raise StaleClaim()
    return source
