"""HTTP contracts for evidence ingestion and search."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from nexus.retrieval.vector_store import SEARCH_LIMIT


class WebIngestionRequest(BaseModel):
    """A public page to index. Safety checks happen before any connection."""

    research_id: uuid.UUID
    url: str = Field(min_length=1, max_length=2048)


class IngestionResponse(BaseModel):
    """One indexed source. ``deduplicated`` means the stored digest was reused."""

    source_id: uuid.UUID
    research_id: uuid.UUID
    status: Literal["indexed"]
    chunk_count: int
    deduplicated: bool


class SearchRequest(BaseModel):
    """A scoped query. ``top_k`` matches the store's search limit."""

    research_id: uuid.UUID
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=SEARCH_LIMIT)

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class SearchMatch(BaseModel):
    """One retrieved passage. The score is cosine similarity, not confidence."""

    chunk_id: uuid.UUID
    source_id: uuid.UUID
    research_id: uuid.UUID
    score: float
    text: str
    page_number: int | None
    url: str | None


class SearchResponse(BaseModel):
    """Evidence only. This response has no generated answer."""

    matches: list[SearchMatch]
