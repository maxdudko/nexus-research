"""Settings validation."""

import os

import pytest
from pydantic import ValidationError
from tests.conftest import make_settings

from nexus.core.config import Settings, _format_settings_error


def test_missing_database_url_names_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("NEXUS_"):
            monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    message = _format_settings_error(exc_info.value)
    assert "NEXUS_DATABASE_URL" in message


def test_blank_openai_api_key_is_none() -> None:
    settings = make_settings(openai_api_key="")
    assert settings.openai_api_key is None


@pytest.mark.parametrize("timeout_seconds", [0, -1])
def test_readiness_timeout_must_be_positive(timeout_seconds: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(readiness_timeout_seconds=timeout_seconds)


def test_unknown_environment_fails() -> None:
    with pytest.raises(ValidationError):
        make_settings(environment="staging")


def test_production_refuses_the_development_password() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            environment="production",
            database_url="postgresql+asyncpg://nexus:nexus@postgres:5432/nexus",
        )

    message = _format_settings_error(exc_info.value)
    assert "NEXUS_DATABASE_URL" in message
    assert "development database password" in message
    assert "nexus:nexus" not in message
    assert "postgresql" not in message


def test_development_allows_the_development_password() -> None:
    settings = make_settings(
        environment="development",
        database_url="postgresql+asyncpg://nexus:nexus@127.0.0.1:5432/nexus",
    )
    assert settings.environment == "development"


def test_production_accepts_a_generated_password() -> None:
    settings = make_settings(
        environment="production",
        database_url="postgresql+asyncpg://nexus:3f9a0c1e7b2d4a6f@postgres:5432/nexus",
    )
    assert settings.environment == "production"
