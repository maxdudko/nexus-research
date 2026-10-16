"""AI settings stay optional and bounded."""

import pytest
from pydantic import SecretStr, ValidationError
from tests.conftest import make_settings


def test_ai_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEXUS_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("NEXUS_OPENAI_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("NEXUS_AI_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("NEXUS_AI_MAX_RETRIES", raising=False)
    monkeypatch.delenv("NEXUS_DISCOVERY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("NEXUS_RAG_COLLECTION", raising=False)
    monkeypatch.delenv("NEXUS_RAG_VECTOR_SIZE", raising=False)
    monkeypatch.delenv("NEXUS_EMBEDDING_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("NEXUS_EMBEDDING_BATCH_SIZE", raising=False)

    settings = make_settings()

    assert settings.openai_api_key is None
    assert settings.tavily_api_key is None
    assert settings.openai_model == "gpt-4o-mini"
    assert settings.openai_embedding_model == "text-embedding-3-small"
    assert settings.rag_collection == "nexus_evidence_v1"
    assert settings.rag_vector_size == 1536
    assert settings.embedding_timeout_seconds == 20.0
    assert settings.embedding_batch_size == 64
    assert settings.ai_timeout_seconds == 20.0
    assert settings.ai_max_retries == 1
    assert settings.discovery_timeout_seconds == 90.0


def test_blank_tavily_api_key_is_none() -> None:
    settings = make_settings(tavily_api_key="")
    assert settings.tavily_api_key is None


def test_secret_values_do_not_appear_in_string_form() -> None:
    settings = make_settings(
        openai_api_key="sk-test-openai",
        tavily_api_key="tvly-test-tavily",
    )
    assert isinstance(settings.openai_api_key, SecretStr)
    assert isinstance(settings.tavily_api_key, SecretStr)
    rendered = f"{settings.openai_api_key} {settings.tavily_api_key}"
    assert "sk-test-openai" not in rendered
    assert "tvly-test-tavily" not in rendered


def test_model_name_is_stripped() -> None:
    settings = make_settings(openai_model="  gpt-4o-mini  ")
    assert settings.openai_model == "gpt-4o-mini"


@pytest.mark.parametrize("model_name", ["", "   "])
def test_blank_model_fails(model_name: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(openai_model=model_name)


@pytest.mark.parametrize(
    "field_name",
    ["ai_timeout_seconds", "discovery_timeout_seconds"],
)
@pytest.mark.parametrize("timeout_seconds", [0, -1])
def test_ai_timeouts_must_be_positive(field_name: str, timeout_seconds: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field_name: timeout_seconds})


@pytest.mark.parametrize("retries", [-1, 3])
def test_retries_outside_range_fail(retries: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(ai_max_retries=retries)


@pytest.mark.parametrize("retries", [0, 2])
def test_retries_at_bounds_are_accepted(retries: int) -> None:
    settings = make_settings(ai_max_retries=retries)
    assert settings.ai_max_retries == retries


def test_embedding_model_does_not_replace_the_chat_model() -> None:
    settings = make_settings(
        openai_model="gpt-4o-mini",
        openai_embedding_model="  text-embedding-3-small  ",
    )
    assert settings.openai_model == "gpt-4o-mini"
    assert settings.openai_embedding_model == "text-embedding-3-small"


@pytest.mark.parametrize("model_name", ["", "   "])
def test_blank_embedding_model_fails(model_name: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(openai_embedding_model=model_name)


@pytest.mark.parametrize("name", ["Nexus", "nexus-evidence", "1abc", "a" * 65])
def test_collection_name_must_be_a_lowercase_identifier(name: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(rag_collection=name)


@pytest.mark.parametrize("size", [0, -1])
def test_vector_size_must_be_positive(size: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(rag_vector_size=size)


@pytest.mark.parametrize("batch_size", [0, 129])
def test_embedding_batch_size_is_bounded(batch_size: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(embedding_batch_size=batch_size)


def test_embedding_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        make_settings(embedding_timeout_seconds=0)
