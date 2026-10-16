"""Thin evidence routes. Extraction and indexing live in the ingestion package."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from nexus.core.config import Settings
from nexus.ingestion.pdf import close_upload, filename_display_name, read_bounded
from nexus.ingestion.service import IngestionResult, IngestionService
from nexus.retrieval.embeddings import OpenAIEmbedder
from nexus.retrieval.service import RetrievalService
from nexus.retrieval.vector_store import EvidenceVectorStore
from nexus.schemas.evidence import (
    IngestionResponse,
    SearchMatch,
    SearchRequest,
    SearchResponse,
    WebIngestionRequest,
)

router = APIRouter()


async def get_ingestion(request: Request) -> AsyncIterator[IngestionService]:
    """Build a request-scoped indexer and close its Qdrant client afterwards."""
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


@router.post("/pdf", response_model=IngestionResponse)
async def ingest_pdf(
    research_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    service: Annotated[IngestionService, Depends(get_ingestion)],
) -> IngestionResult:
    """Index a text PDF. The upload is closed before the response returns."""
    try:
        data = await read_bounded(file, service.settings.pdf_max_bytes)
        return await service.ingest_pdf(
            research_id,
            data,
            filename_display_name(file.filename),
        )
    finally:
        await close_upload(file)


@router.post("/web", response_model=IngestionResponse)
async def ingest_web(
    body: WebIngestionRequest,
    service: Annotated[IngestionService, Depends(get_ingestion)],
) -> IngestionResult:
    """Index one public HTML page."""
    return await service.ingest_web(body.research_id, body.url)


async def get_retrieval(request: Request) -> AsyncIterator[RetrievalService]:
    """Build a request-scoped searcher and close its Qdrant client afterwards."""
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
    body: SearchRequest,
    service: Annotated[RetrievalService, Depends(get_retrieval)],
) -> SearchResponse:
    """Return scoped evidence. The score is similarity, not an answer."""
    matches = await service.search(body.research_id, body.query, body.top_k)
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
