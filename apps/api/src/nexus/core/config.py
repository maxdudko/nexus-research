"""Process configuration loaded once at startup."""

import sys
from functools import lru_cache
from typing import Literal

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    SecretStr,
    ValidationError,
    field_validator,
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
    tavily_api_key: SecretStr | None = None
    ai_timeout_seconds: float = Field(default=20.0, gt=0)
    ai_max_retries: int = Field(default=1, ge=0, le=2)
    discovery_timeout_seconds: float = Field(default=90.0, gt=0)

    @field_validator("openai_api_key", "tavily_api_key", mode="before")
    @classmethod
    def blank_secret(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @field_validator("openai_model", mode="before")
    @classmethod
    def strip_openai_model(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
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
