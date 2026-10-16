"""Which source digests current retrieval may return."""

import uuid

from nexus.models.source import SourceStatus
from nexus.retrieval.versions import VersionSnapshot, accepted_versions

DIGEST = "ab" * 32
ATTEMPT = "cd" * 32


def _snapshot(
    status: SourceStatus,
    content_hash: str | None,
    indexing_content_hash: str | None = ATTEMPT,
) -> VersionSnapshot:
    return VersionSnapshot(
        source_id=uuid.uuid4(),
        status=status,
        content_hash=content_hash,
        indexing_content_hash=indexing_content_hash,
    )


def test_accepted_versions_keep_the_last_digest() -> None:
    indexed = _snapshot(SourceStatus.INDEXED, DIGEST, None)
    failed = _snapshot(SourceStatus.FAILED, DIGEST)
    indexing = _snapshot(SourceStatus.INDEXING, DIGEST)
    records = [
        indexed,
        failed,
        indexing,
        _snapshot(SourceStatus.PENDING, DIGEST),
        _snapshot(SourceStatus.FAILED, None),
        _snapshot(SourceStatus.INDEXING, None),
    ]

    accepted = accepted_versions(records)

    assert [(item.source_id, item.content_hash) for item in accepted] == [
        (indexed.source_id, DIGEST),
        (failed.source_id, DIGEST),
        (indexing.source_id, DIGEST),
    ]
    assert all(item.content_hash != ATTEMPT for item in accepted)
