"""Process configuration loaded once at startup."""

import re
import sys
from functools import lru_cache
from typing import Literal, Self

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings for the API process."""

    model_config = SettingsConfigDict(
        env_prefix="NEXUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: PostgresDsn
    qdrant_url: AnyHttpUrl
    readiness_timeout_seconds: float = Field(default=2.0, gt=0)
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    tavily_api_key: SecretStr | None = None
    ai_timeout_seconds: float = Field(default=20.0, gt=0)
    ai_max_retries: int = Field(default=1, ge=0, le=2)
    discovery_timeout_seconds: float = Field(default=90.0, gt=0)
    rag_collection: str = "nexus_evidence_v1"
    rag_vector_size: int = Field(default=1536, gt=0)
    embedding_timeout_seconds: float = Field(default=20.0, gt=0)
    embedding_batch_size: int = Field(default=64, ge=1, le=128)
    pdf_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    pdf_max_pages: int = Field(default=100, gt=0)
    web_max_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    web_max_redirects: int = Field(default=3, ge=0, le=5)
    chunk_tokens: int = Field(default=800, gt=0)
    chunk_overlap_tokens: int = Field(default=120, ge=0)
    max_chunks: int = Field(default=200, gt=0)
    extracted_text_max_chars: int = Field(default=1_000_000, gt=0)
    ingestion_timeout_seconds: float = Field(default=60.0, gt=0)
    web_timeout_seconds: float = Field(default=10.0, gt=0)
    graph_max_iterations: int = Field(default=2, ge=1, le=2)
    graph_max_search_calls: int = Field(default=6, ge=1, le=100)
    graph_max_llm_calls: int = Field(default=13, ge=1, le=100)
    graph_max_web_pages: int = Field(default=5, ge=1, le=20)
    graph_max_passages: int = Field(default=12, ge=1, le=50)
    graph_deadline_seconds: float = Field(default=180.0, gt=0, le=600)
    graph_excerpt_chars: int = Field(default=500, ge=1, le=2000)

    @model_validator(mode="after")
    def overlap_fits_in_a_chunk(self) -> Self:
        if self.chunk_overlap_tokens >= self.chunk_tokens:
            raise ValueError("chunk overlap must be smaller than the chunk size")
        return self

    @field_validator("openai_api_key", "tavily_api_key", mode="before")
    @classmethod
    def blank_secret(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @field_validator("openai_model", "openai_embedding_model", mode="before")
    @classmethod
    def strip_model_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("rag_collection", mode="before")
    @classmethod
    def collection_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", stripped) is None:
            raise ValueError("must be a lowercase identifier")
        return stripped


def _format_settings_error(exc: ValidationError) -> str:
    lines = [
        (
            "Invalid configuration. Set these variables in the environment "
            "or apps/api/.env:"
        ),
    ]
    for error in exc.errors():
        location = error.get("loc", ())
        field_name = str(location[-1]) if location else "unknown"
        env_name = f"NEXUS_{field_name.upper()}"
        lines.append(f"- {env_name}: {error['msg']}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once. Exit with a variable list when configuration is invalid."""
    try:
        # Values come from the environment, which mypy cannot see on BaseSettings.
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        print(_format_settings_error(exc), file=sys.stderr)
        raise SystemExit(1) from None
