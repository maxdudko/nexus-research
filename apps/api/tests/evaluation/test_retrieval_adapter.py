"""Map Qdrant chunk ids back to semantic ids with a fake embedder."""

import hashlib
import uuid

import pytest
from tests.conftest import make_settings
from tests.integration.conftest import require_local_qdrant

from nexus.core.config import get_settings
from nexus.evaluation.datasets import content_hash, load_dataset
from nexus.evaluation.retrieval import evidence_point, ranks_from_matches
from nexus.retrieval.vector_store import EvidenceVectorStore, create_qdrant_client
from nexus.retrieval.versions import AcceptedVersion

pytestmark = pytest.mark.integration

WIDTH = 8
COLLECTION_PREFIX = "nexus_eval_it_"
APPLICATION_COLLECTION = "nexus_evidence_v1"


def test_evidence_point_uses_the_catalog_chunk_id() -> None:
    dataset = load_dataset()
    chunk = dataset.catalog.chunks["pdf-pages#c0"]
    point = evidence_point(chunk, [0.0] * 8, claim_token=dataset.catalog.claim_token)

    assert point.content_hash == content_hash(chunk.text)
    assert point.page_number == 2
    assert point.url is None
    from nexus.retrieval.ids import evidence_point_id

    assert evidence_point_id(
        point.source_id,
        point.content_hash,
        point.claim_token,
        point.chunk_index,
    ) == dataset.catalog.chunk_id("pdf-pages#c0")


async def test_fake_embedder_maps_matches_back_to_semantic_ids() -> None:
    configured = str(get_settings().qdrant_url)
    require_local_qdrant(configured)
    name = f"{COLLECTION_PREFIX}{uuid.uuid4().hex}"
    if name == APPLICATION_COLLECTION or not name.startswith(COLLECTION_PREFIX):
        raise RuntimeError("refusing to use the application collection")
    settings = make_settings(
        qdrant_url=configured,
        rag_collection=name,
        rag_vector_size=WIDTH,
    )
    client = create_qdrant_client(settings)
    store = EvidenceVectorStore(settings, client)
    dataset = load_dataset()
    research_id = dataset.catalog.chunks["web-limits#c0"].research_id
    chunks = [
        chunk
        for chunk in dataset.catalog.chunks.values()
        if chunk.research_id == research_id
    ]
    try:
        points = [
            evidence_point(
                chunk,
                _embed(chunk.text),
                claim_token=dataset.catalog.claim_token,
            )
            for chunk in chunks
        ]
        await store.upsert(research_id=research_id, points=points)
        accepted: set[uuid.UUID] = set()
        for chunk in chunks:
            if chunk.source_id in accepted:
                continue
            accepted.add(chunk.source_id)
            await store.accept_claim(
                research_id=chunk.research_id,
                source_id=chunk.source_id,
                claim_token=dataset.catalog.claim_token,
            )
        target = dataset.catalog.chunks["web-limits#c0"]
        matches = await store.search_accepted(
            research_id=research_id,
            vector=_embed(target.text),
            limit=5,
            versions=[
                AcceptedVersion(
                    source_id=chunk.source_id,
                    content_hash=content_hash(chunk.text),
                )
                for chunk in chunks
            ],
        )
        ranked = ranks_from_matches(matches, dataset.catalog)
        assert ranked[0] == "web-limits#c0"
        assert matches[0].chunk_id == dataset.catalog.chunk_id("web-limits#c0")
    finally:
        if name.startswith(COLLECTION_PREFIX) and name != APPLICATION_COLLECTION:
            if await client.collection_exists(name):
                await client.delete_collection(name)
        await client.close()


def _embed(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    values = [byte / 255 for byte in digest[:WIDTH]]
    if not any(values):
        values[0] = 1.0
    return values
