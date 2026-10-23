"""Thin evidence routes. Extraction and indexing live in the ingestion package."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from nexus.api.ownership import (
    OwnedEvidence,
    authorize_pdf,
    authorize_search,
    authorize_web,
)
from nexus.core.config import Settings
from nexus.ingestion.pdf import close_upload, filename_display_name, read_bounded
from nexus.ingestion.service import IngestionResult, IngestionService
from nexus.retrieval.embeddings import OpenAIEmbedder
from nexus.retrieval.service import RetrievalService
from nexus.retrieval.vector_store import EvidenceVectorStore
from nexus.schemas.evidence import (
    IngestionResponse,
    SearchMatch,
    SearchResponse,
)

router = APIRouter()


async def get_ingestion(
    request: Request,
    scope: Annotated[OwnedEvidence, Depends(authorize_pdf)],
) -> AsyncIterator[IngestionService]:
    """Open Qdrant only after this PDF belongs to an owned scope."""
    del scope
    async with _ingestion(request) as service:
        yield service


async def get_web_ingestion(
    request: Request,
    scope: Annotated[OwnedEvidence, Depends(authorize_web)],
) -> AsyncIterator[IngestionService]:
    """Open Qdrant only after this page belongs to an owned scope."""
    del scope
    async with _ingestion(request) as service:
        yield service


@router.post("/pdf", response_model=IngestionResponse)
async def ingest_pdf(
    scope: Annotated[OwnedEvidence, Depends(authorize_pdf)],
    service: Annotated[IngestionService, Depends(get_ingestion)],
) -> IngestionResult:
    """Index a text PDF. The upload is closed before the response returns."""
    upload = scope.upload
    if upload is None:
        raise AssertionError("pdf upload missing")
    try:
        data = await read_bounded(upload, service.settings.pdf_max_bytes)
        return await service.ingest_pdf(
            scope.research_id,
            data,
            filename_display_name(upload.filename),
            owner_id=scope.user.id,
        )
    finally:
        await close_upload(upload)


@router.post("/web", response_model=IngestionResponse)
async def ingest_web(
    scope: Annotated[OwnedEvidence, Depends(authorize_web)],
    service: Annotated[IngestionService, Depends(get_web_ingestion)],
) -> IngestionResult:
    """Index one public HTML page."""
    if scope.web is None:
        raise AssertionError("web request missing")
    return await service.ingest_web(
        scope.research_id,
        scope.web.url,
        owner_id=scope.user.id,
    )


async def get_retrieval(
    request: Request,
    scope: Annotated[OwnedEvidence, Depends(authorize_search)],
) -> AsyncIterator[RetrievalService]:
    """Open Qdrant only after this query belongs to an owned scope."""
    del scope
    settings: Settings = request.app.state.settings
    store = EvidenceVectorStore(settings)
    service = RetrievalService(
        request.app.state.session_factory,
        store,
        OpenAIEmbedder(settings),
    )
    try:
        yield service
    finally:
        await store.close()


@router.post("/search", response_model=SearchResponse)
async def search_evidence(
    scope: Annotated[OwnedEvidence, Depends(authorize_search)],
    service: Annotated[RetrievalService, Depends(get_retrieval)],
) -> SearchResponse:
    """Return scoped evidence. The score is similarity, not an answer."""
    if scope.search is None:
        raise AssertionError("search request missing")
    matches = await service.search(
        scope.research_id,
        scope.search.query,
        scope.search.top_k,
        owner_id=scope.user.id,
    )
    return SearchResponse(
        matches=[
            SearchMatch(
                chunk_id=match.chunk_id,
                source_id=match.source_id,
                research_id=match.research_id,
                score=match.score,
                text=match.text,
                page_number=match.page_number,
                url=match.url,
            )
            for match in matches
        ],
    )


@asynccontextmanager
async def _ingestion(request: Request) -> AsyncIterator[IngestionService]:
    settings: Settings = request.app.state.settings
    store = EvidenceVectorStore(settings)
    service = IngestionService(
        request.app.state.session_factory,
        store,
        OpenAIEmbedder(settings),
        settings,
    )
    try:
        yield service
    finally:
        await store.close()
