"""HTTP contracts for evidence ingestion."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


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
