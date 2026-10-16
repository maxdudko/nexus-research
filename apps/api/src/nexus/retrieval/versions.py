"""Accepted evidence versions for Group C retrieval.

An accepted version is the last digest stored on ``content_hash``. ``INDEXED``,
``FAILED``, and ``INDEXING`` keep that digest searchable. ``PENDING``, a null
hash, and ``indexing_content_hash`` are not search keys. A failed first attempt
has no digest. A failed replacement hides only the failed attempt.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from nexus.models.source import CONTENT_HASH, SourceStatus

_SEARCHABLE = frozenset(
    {
        SourceStatus.INDEXED,
        SourceStatus.FAILED,
        SourceStatus.INDEXING,
    },
)


@dataclass(frozen=True)
class VersionSnapshot:
    """The source columns that decide which digest retrieval may use."""

    source_id: uuid.UUID
    status: SourceStatus
    content_hash: str | None
    indexing_content_hash: str | None = None


@dataclass(frozen=True)
class AcceptedVersion:
    """One source digest that current retrieval may return."""

    source_id: uuid.UUID
    content_hash: str


def accepted_versions(records: Sequence[VersionSnapshot]) -> list[AcceptedVersion]:
    """Return the last accepted digest for each searchable source row."""
    accepted: list[AcceptedVersion] = []
    for record in records:
        digest = record.content_hash
        if record.status not in _SEARCHABLE or digest is None:
            continue
        if CONTENT_HASH.fullmatch(digest) is None:
            continue
        accepted.append(
            AcceptedVersion(source_id=record.source_id, content_hash=digest),
        )
    return accepted
