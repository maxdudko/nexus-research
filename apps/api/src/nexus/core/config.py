"""Process configuration loaded once at startup."""

import hmac
import re
import sys
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    stream_heartbeat_seconds: float = Field(default=15.0, gt=0, le=60)
    stream_max_concurrent: int = Field(default=2, ge=1, le=8)
    stream_cancel_persist_seconds: float = Field(default=2.0, gt=0, le=5)
    otel_enabled: bool = False
    otel_endpoint: AnyHttpUrl | None = None
    otel_service_name: str = "nexus-research"
    otel_sample_ratio: float = Field(default=1.0, ge=0, le=1)
    log_format: Literal["json", "text"] = "json"
    tavily_usd_per_search: Decimal | None = Field(default=None, ge=0)
    session_pepper: SecretStr
    session_absolute_seconds: int = Field(default=12 * 60 * 60, gt=0, le=24 * 60 * 60)
    session_idle_seconds: int = Field(default=4 * 60 * 60, gt=0, le=24 * 60 * 60)
    session_touch_seconds: int = Field(default=5 * 60, gt=0, le=60 * 60)
    cookie_insecure_loopback: bool = False
    cookie_secure: bool | None = None
    trusted_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    login_max_email_failures: int = Field(default=5, ge=1, le=20)
    login_max_ip_failures: int = Field(default=30, ge=1, le=100)
    login_failure_window_seconds: int = Field(default=15 * 60, ge=60, le=60 * 60)

    @model_validator(mode="after")
    def overlap_fits_in_a_chunk(self) -> Self:
        if self.chunk_overlap_tokens >= self.chunk_tokens:
            raise ValueError("chunk overlap must be smaller than the chunk size")
        return self

    @model_validator(mode="after")
    def session_pepper_is_independent(self) -> Self:
        pepper = self.session_pepper.get_secret_value()
        if len(pepper.encode("utf-8")) < 32:
            raise ValueError("session pepper must be at least 32 bytes")
        password = _database_password(self.database_url)
        if (
            password is not None
            and len(password) == len(pepper)
            and hmac.compare_digest(pepper, password)
        ):
            raise ValueError("session pepper must not match the database password")
        return self

    def secure_cookies(self) -> bool:
        """Return the resolved Secure flag. Validation always sets it."""
        return bool(self.cookie_secure)

    @field_validator("database_url")
    @classmethod
    def no_development_password(
        cls, value: PostgresDsn, info: ValidationInfo
    ) -> PostgresDsn:
        if info.data.get("environment") != "production":
            return value
        if any(host.get("password") == "nexus" for host in value.hosts()):
            raise ValueError(
                "the development database password is not allowed in production"
            )
        return value

    @field_validator("session_pepper", mode="before")
    @classmethod
    def session_pepper_present(cls, value: object) -> object:
        if value is None or value == "":
            raise ValueError("must not be empty")
        return value

    @field_validator("trusted_origins", mode="before")
    @classmethod
    def split_trusted_origins(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("trusted_origins")
    @classmethod
    def trusted_origin_list(cls, value: list[str], info: ValidationInfo) -> list[str]:
        cleaned: list[str] = []
        for origin in value:
            if _ORIGIN.fullmatch(origin) is None:
                raise ValueError("must be an origin without a path")
            if origin not in cleaned:
                cleaned.append(origin)
        if cleaned:
            return cleaned
        if info.data.get("environment") == "production":
            raise ValueError("trusted origins are required in production")
        return ["http://127.0.0.1:3000", "http://localhost:3000"]

    @field_validator("cookie_secure")
    @classmethod
    def resolve_cookie_secure(cls, value: bool | None, info: ValidationInfo) -> bool:
        environment = info.data.get("environment")
        loopback = bool(info.data.get("cookie_insecure_loopback"))
        resolved = environment == "production" if value is None else value
        if environment == "production" and not resolved and not loopback:
            raise ValueError(
                "production cookies must be Secure unless "
                "NEXUS_COOKIE_INSECURE_LOOPBACK is true"
            )
        return resolved

    @field_validator(
        "openai_api_key",
        "tavily_api_key",
        "otel_endpoint",
        "tavily_usd_per_search",
        mode="before",
    )
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

    @field_validator("otel_service_name", mode="before")
    @classmethod
    def service_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped or len(stripped) > 64 or "://" in stripped:
            raise ValueError("must be a short service name")
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


_ORIGIN = re.compile(r"^https?://(?:localhost|\[::1\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?$")


def _database_password(url: PostgresDsn) -> str | None:
    for host in url.hosts():
        password = host.get("password")
        if isinstance(password, str) and password:
            return password
    return None


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
