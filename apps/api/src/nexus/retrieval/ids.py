"""Deterministic Qdrant point identifiers.

The same source, digest, and chunk index always produce the same UUID, so a
repeated upsert overwrites one point. Changing the namespace or the ``v1``
prefix is an index migration, not an in-place rewrite.
"""

import uuid

POINT_NAMESPACE = uuid.UUID("8d4e2c1a-6b7f-4e90-9c3d-1a2b3c4d5e6f")


def evidence_point_id(
    source_id: uuid.UUID,
    content_hash: str,
    chunk_index: int,
) -> uuid.UUID:
    """Return the point id, which is also the citation chunk id."""
    if chunk_index < 0:
        raise ValueError("chunk_index must be non-negative")
    name = f"v1|{source_id}|{content_hash}|{chunk_index}"
    return uuid.uuid5(POINT_NAMESPACE, name)
