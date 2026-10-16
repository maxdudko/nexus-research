"""Deterministic Qdrant point identifiers.

The same source, digest, claim token, and chunk index always produce the same
UUID. A later attempt uses a new token, so its upsert writes different ids and
cannot replace an accepted point. Changing the namespace or the ``v2`` prefix
is an index migration, not an in-place rewrite.
"""

import uuid

POINT_NAMESPACE = uuid.UUID("8d4e2c1a-6b7f-4e90-9c3d-1a2b3c4d5e6f")


def evidence_point_id(
    source_id: uuid.UUID,
    content_hash: str,
    claim_token: uuid.UUID,
    chunk_index: int,
) -> uuid.UUID:
    """Return the point id, which is also the citation chunk id."""
    if chunk_index < 0:
        raise ValueError("chunk_index must be non-negative")
    if not isinstance(claim_token, uuid.UUID):
        raise ValueError("claim_token must be a UUID")
    name = f"v2|{source_id}|{content_hash}|{claim_token}|{chunk_index}"
    return uuid.uuid5(POINT_NAMESPACE, name)
