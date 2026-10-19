"""Opt-in retrieval check. Default pytest does not collect this module.

The result is informational. It is not compared with docs/evaluation/baseline.json.
"""

import os
import uuid

import pytest
from tests.conftest import make_settings
from tests.integration.conftest import require_local_qdrant

from nexus.core.config import get_settings
from nexus.evaluation.datasets import content_hash, load_dataset
from nexus.evaluation.retrieval import evidence_point, ranks_from_matches
from nexus.retrieval.embeddings import OpenAIEmbedder
from nexus.retrieval.vector_store import EvidenceVectorStore, create_qdrant_client
from nexus.retrieval.versions import AcceptedVersion

pytestmark = pytest.mark.live

COLLECTION_PREFIX = "nexus_eval_live_"
APPLICATION_COLLECTION = "nexus_evidence_v1"


async def test_live_embeddings_map_back_to_semantic_ids() -> None:
    if os.environ.get("NEXUS_RUN_LIVE_AI") != "1":
        pytest.skip("set NEXUS_RUN_LIVE_AI=1 to embed the fixture")
    key = os.environ.get("NEXUS_OPENAI_API_KEY")
    if not key:
        pytest.skip("NEXUS_OPENAI_API_KEY is required")
    configured = str(get_settings().qdrant_url)
    require_local_qdrant(configured)
    name = f"{COLLECTION_PREFIX}{uuid.uuid4().hex}"
    if name == APPLICATION_COLLECTION or not name.startswith(COLLECTION_PREFIX):
        raise RuntimeError("refusing to use the application collection")
    settings = make_settings(
        openai_api_key=key,
        qdrant_url=configured,
        rag_collection=name,
    )
    client = create_qdrant_client(settings)
    store = EvidenceVectorStore(settings, client)
    embedder = OpenAIEmbedder(settings)
    dataset = load_dataset()
    research_id = dataset.catalog.chunks["web-limits#c0"].research_id
    chunks = [
        chunk
        for chunk in dataset.catalog.chunks.values()
        if chunk.research_id == research_id
    ]
    try:
        vectors = await embedder.embed_documents([chunk.text for chunk in chunks])
        points = [
            evidence_point(
                chunk,
                vector,
                claim_token=dataset.catalog.claim_token,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
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
        query = await embedder.embed_query(target.text)
        matches = await store.search_accepted(
            research_id=research_id,
            vector=query,
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
        assert ranked
        assert set(ranked) <= {chunk.semantic_id for chunk in chunks}
        assert ranked[0] == "web-limits#c0"
    finally:
        if name.startswith(COLLECTION_PREFIX) and name != APPLICATION_COLLECTION:
            if await client.collection_exists(name):
                await client.delete_collection(name)
        await client.close()
