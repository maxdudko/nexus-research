"""Research-scoped evidence search.

Phase 4 calls this service. The route does too. A search reads the accepted
digest of each source in one research, embeds the query, and asks Qdrant for
those points. An empty scope does not embed and does not query Qdrant.
``search()`` on the store stays a diagnostic and is not used here.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.ai.errors import not_found
from nexus.identity.scopes import owned_scope
from nexus.models.source import Source
from nexus.retrieval.embeddings import Embedder
from nexus.retrieval.vector_store import EvidenceMatch, EvidenceVectorStore
from nexus.retrieval.versions import AcceptedVersion, VersionSnapshot, accepted_versions


class RetrievalService:
    """Return scored passages for one research scope."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        store: EvidenceVectorStore,
        embedder: Embedder,
    ) -> None:
        self._sessions = session_factory
        self._store = store
        self._embedder = embedder

    async def search(
        self,
        research_id: uuid.UUID,
        query: str,
        top_k: int,
        *,
        owner_id: uuid.UUID,
    ) -> list[EvidenceMatch]:
        """Search the accepted evidence for ``research_id``.

        Matches keep the provenance stored on the point. A missing page or URL
        stays missing. Versions are loaded again after Qdrant returns, and a
        digest that was replaced in that window is dropped.
        """
        found = await owned_scope(self._sessions, owner_id, research_id)
        if found is None:
            raise not_found()
        versions = await self._versions(research_id)
        if not versions:
            return []
        vector = await self._embedder.embed_query(query)
        matches = await self._store.search_accepted(
            research_id=research_id,
            vector=vector,
            limit=top_k,
            versions=versions,
        )
        current = await self._versions(research_id)
        allowed = {(version.source_id, version.content_hash) for version in current}
        return [
            match
            for match in matches
            if match.research_id == research_id
            and (match.source_id, match.content_hash) in allowed
        ]

    async def _versions(self, research_id: uuid.UUID) -> list[AcceptedVersion]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(Source).where(Source.research_id == research_id),
                )
            ).all()
        snapshots = [
            VersionSnapshot(
                source_id=row.id,
                status=row.status,
                content_hash=row.content_hash,
                indexing_content_hash=row.indexing_content_hash,
            )
            for row in rows
        ]
        return accepted_versions(snapshots)
