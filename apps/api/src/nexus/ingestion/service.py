"""Index one prepared document with the existing source claim.

PDF bytes and fetched HTML are not stored. A lost PDF index is rebuilt by
uploading the file again. Embedding and Qdrant upserts stay outside the row
lock. A stale attempt deletes only the points tagged with its own token.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.ai.errors import (
    ResearchError,
    indexing_in_progress,
    ingestion_timeout,
    not_found,
    payload_too_large,
)
from nexus.core.config import Settings
from nexus.identity.scopes import owned_scope
from nexus.ingestion.chunking import (
    Chunker,
    TextChunk,
    pdf_content_hash,
    web_content_hash,
)
from nexus.ingestion.pdf import extract_pdf_pages
from nexus.ingestion.transport import FetchedPage, fetch_public_page
from nexus.ingestion.web import extract_article
from nexus.models.source import ERROR_CODE, Source, SourceStatus, SourceType
from nexus.retrieval.embeddings import Embedder
from nexus.retrieval.vector_store import EvidencePoint, EvidenceVectorStore
from nexus.sources.lifecycle import (
    ClaimAction,
    StaleClaim,
    abandon_claim,
    claim_source,
    finish_failed,
    finish_indexed,
    retire_other_claims,
)

logger = logging.getLogger(__name__)

PageFetcher = Callable[[str], Awaitable[FetchedPage]]


@dataclass(frozen=True)
class IngestionResult:
    """The HTTP success body for one indexing request."""

    source_id: uuid.UUID
    research_id: uuid.UUID
    status: Literal["indexed"]
    chunk_count: int
    deduplicated: bool


@dataclass
class _Attempt:
    research_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    token: uuid.UUID | None = None
    digest: str | None = None
    upsert_started: bool = False
    published: bool = False
    result: IngestionResult | None = None


class IngestionService:
    """Claim, embed, and publish one PDF or web document."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        store: EvidenceVectorStore,
        embedder: Embedder,
        settings: Settings,
        *,
        chunker: Chunker | None = None,
        fetch_page: PageFetcher | None = None,
    ) -> None:
        self._sessions = session_factory
        self._store = store
        self._embedder = embedder
        self._settings = settings
        self._chunker = chunker or Chunker(
            chunk_tokens=settings.chunk_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
            max_chunks=settings.max_chunks,
            model_name=settings.openai_embedding_model,
        )
        self._fetch_page = fetch_page

    async def _require_owner(self, owner_id: uuid.UUID, research_id: uuid.UUID) -> None:
        found = await owned_scope(self._sessions, owner_id, research_id)
        if found is None:
            raise not_found()

    @property
    def settings(self) -> Settings:
        return self._settings

    async def ingest_pdf(
        self,
        research_id: uuid.UUID,
        data: bytes,
        display_name: str | None,
        *,
        owner_id: uuid.UUID,
    ) -> IngestionResult:
        """Extract a PDF and index its pages. The file bytes are discarded."""
        await self._require_owner(owner_id, research_id)
        attempt = _Attempt()

        async def prepare() -> IngestionResult:
            pages = await asyncio.to_thread(
                extract_pdf_pages,
                data,
                max_pages=self._settings.pdf_max_pages,
                max_chars=self._settings.extracted_text_max_chars,
            )
            chunks = self._chunker.chunk_pages(pages)
            return await self._index(
                attempt,
                research_id=research_id,
                source_type=SourceType.PDF,
                uri=None,
                content_hash=pdf_content_hash([text for _, text in pages]),
                display_name=display_name,
                chunks=chunks,
                url=None,
                title=display_name,
            )

        return await self._deadline(attempt, prepare)

    async def ingest_web(
        self,
        research_id: uuid.UUID,
        url: str,
        *,
        owner_id: uuid.UUID,
    ) -> IngestionResult:
        """Fetch one public page and index its article text."""
        await self._require_owner(owner_id, research_id)
        attempt = _Attempt()

        async def prepare() -> IngestionResult:
            fetched = await self._fetch(url)
            article, title = await asyncio.to_thread(
                extract_article,
                fetched.body,
                fetched.url,
            )
            if len(article) > self._settings.extracted_text_max_chars:
                logger.warning("web rejected: text limit")
                raise payload_too_large()
            chunks = self._chunker.chunk_pages([(None, article)])
            return await self._index(
                attempt,
                research_id=research_id,
                source_type=SourceType.WEB,
                uri=fetched.url,
                content_hash=web_content_hash(article),
                display_name=title,
                chunks=chunks,
                url=fetched.url,
                title=title,
            )

        return await self._deadline(attempt, prepare)

    async def _deadline(
        self,
        attempt: _Attempt,
        prepare: Callable[[], Awaitable[IngestionResult]],
    ) -> IngestionResult:
        try:
            async with asyncio.timeout(self._settings.ingestion_timeout_seconds):
                return await prepare()
        except TimeoutError:
            if attempt.published and attempt.result is not None:
                logger.warning("superseded delete interrupted")
                return attempt.result
            await self._recover(attempt, "ingestion_timeout")
            raise ingestion_timeout() from None
        except asyncio.CancelledError:
            await self._recover(attempt, "cancelled")
            raise

    async def _fetch(self, url: str) -> FetchedPage:
        if self._fetch_page is not None:
            return await self._fetch_page(url)
        return await fetch_public_page(
            url,
            max_bytes=self._settings.web_max_bytes,
            max_redirects=self._settings.web_max_redirects,
            timeout_seconds=self._settings.web_timeout_seconds,
        )

    async def _index(
        self,
        attempt: _Attempt,
        *,
        research_id: uuid.UUID,
        source_type: SourceType,
        uri: str | None,
        content_hash: str,
        display_name: str | None,
        chunks: list[TextChunk],
        url: str | None,
        title: str | None,
    ) -> IngestionResult:
        async with self._sessions() as session:
            async with session.begin():
                claim = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=source_type,
                    uri=uri,
                    content_hash=content_hash,
                    display_name=display_name,
                )
            if claim.action is ClaimAction.DEDUPLICATED:
                source = await session.get(Source, claim.source_id)
                count = source.chunk_count if source is not None else 0
                return IngestionResult(
                    source_id=claim.source_id,
                    research_id=research_id,
                    status="indexed",
                    chunk_count=count,
                    deduplicated=True,
                )
        if claim.token is None:
            raise indexing_in_progress()
        attempt.research_id = research_id
        attempt.source_id = claim.source_id
        attempt.token = claim.token
        attempt.digest = content_hash
        if not await self._owns(claim.source_id, claim.token):
            return await self._stale(claim.source_id, content_hash)
        try:
            vectors = await self._embedder.embed_documents(
                [chunk.text for chunk in chunks],
            )
        except ResearchError as exc:
            await self._fail(attempt, exc.code)
            raise
        if not await self._owns(claim.source_id, claim.token):
            return await self._stale(claim.source_id, content_hash)
        points = [
            EvidencePoint(
                research_id=research_id,
                source_id=claim.source_id,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                vector=vectors[index],
                content_hash=content_hash,
                claim_token=claim.token,
                page_number=chunk.page_number,
                url=url,
                title=title,
            )
            for index, chunk in enumerate(chunks)
        ]
        attempt.upsert_started = True
        try:
            await self._store.upsert(research_id=research_id, points=points)
        except ResearchError as exc:
            await self._fail(attempt, exc.code)
            raise
        if not await self._owns(claim.source_id, claim.token):
            await abandon_claim(
                self._store,
                research_id=research_id,
                source_id=claim.source_id,
                token=claim.token,
            )
            return await self._stale(claim.source_id, content_hash)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await retire_other_claims(
                        session,
                        self._store,
                        source_id=claim.source_id,
                        token=claim.token,
                    )
                    await finish_indexed(
                        session,
                        source_id=claim.source_id,
                        token=claim.token,
                        content_hash=content_hash,
                        chunk_count=len(chunks),
                    )
        except StaleClaim:
            await self._abandon_own(attempt)
            return await self._stale(claim.source_id, content_hash)
        except TimeoutError:
            raise
        except Exception as exc:
            try:
                await self._fail(attempt, _failure_code(exc))
            except Exception as cleanup_exc:
                logger.warning(
                    "ingestion cleanup failed: %s",
                    type(cleanup_exc).__name__,
                )
            raise
        result = IngestionResult(
            source_id=claim.source_id,
            research_id=research_id,
            status="indexed",
            chunk_count=len(chunks),
            deduplicated=False,
        )
        attempt.published = True
        attempt.result = result
        await self._drop_superseded(attempt)
        return result

    async def _owns(self, source_id: uuid.UUID, token: uuid.UUID) -> bool:
        async with self._sessions() as session:
            source = await session.get(Source, source_id)
        return (
            source is not None
            and source.status is SourceStatus.INDEXING
            and source.indexing_token == token
        )

    async def _stale(self, source_id: uuid.UUID, digest: str) -> IngestionResult:
        async with self._sessions() as session:
            source = await session.get(Source, source_id)
        if (
            source is not None
            and source.status is SourceStatus.INDEXED
            and source.content_hash == digest
        ):
            return IngestionResult(
                source_id=source_id,
                research_id=source.research_id,
                status="indexed",
                chunk_count=source.chunk_count,
                deduplicated=True,
            )
        raise indexing_in_progress()

    async def _drop_superseded(self, attempt: _Attempt) -> None:
        """Delete other accepted tokens. A failure leaves the new version in place."""
        if (
            attempt.research_id is None
            or attempt.source_id is None
            or attempt.token is None
        ):
            return
        try:
            await self._store.delete_other_claims(
                research_id=attempt.research_id,
                source_id=attempt.source_id,
                claim_token=attempt.token,
            )
        except Exception as exc:
            logger.warning("superseded delete failed: %s", type(exc).__name__)

    async def _abandon_own(self, attempt: _Attempt) -> None:
        if (
            not attempt.upsert_started
            or attempt.research_id is None
            or attempt.source_id is None
            or attempt.token is None
        ):
            return
        try:
            await abandon_claim(
                self._store,
                research_id=attempt.research_id,
                source_id=attempt.source_id,
                token=attempt.token,
            )
        except Exception as exc:
            logger.warning("ingestion abandon failed: %s", type(exc).__name__)

    async def _recover(self, attempt: _Attempt, code: str) -> None:
        try:
            await self._fail(attempt, code)
        except Exception as exc:
            logger.warning("ingestion cleanup failed: %s", type(exc).__name__)

    async def _fail(self, attempt: _Attempt, code: str) -> None:
        if attempt.published:
            return
        if (
            attempt.token is None
            or attempt.source_id is None
            or attempt.research_id is None
        ):
            return
        await self._abandon_own(attempt)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await finish_failed(
                        session,
                        source_id=attempt.source_id,
                        token=attempt.token,
                        error_code=_public_code(code),
                    )
        except StaleClaim:
            return
        except Exception as exc:
            logger.warning("ingestion fail status failed: %s", type(exc).__name__)
            raise


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, ResearchError):
        return _public_code(exc.code)
    if isinstance(exc, SQLAlchemyError):
        logger.warning("ingestion publish failed: %s", type(exc).__name__)
    return "provider_unavailable"


def _public_code(code: str) -> str:
    if ERROR_CODE.fullmatch(code) is None:
        return "provider_unavailable"
    return code
