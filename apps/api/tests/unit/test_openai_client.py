"""The OpenAI client is built on demand and does not call the network."""

import pytest
from pydantic import SecretStr
from tests.conftest import make_settings

from nexus.ai.client import OpenAIStructuredModel, build_chat_model
from nexus.ai.errors import ResearchError
from nexus.schemas.research import ResearchPlan


def test_missing_key_does_not_construct_chat_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ChatOpenAI was constructed")

    monkeypatch.setattr("nexus.ai.client.ChatOpenAI", fail)

    with pytest.raises(ResearchError) as exc_info:
        build_chat_model(make_settings(openai_api_key=None))

    error = exc_info.value
    assert error.code == "provider_not_configured"
    assert error.status_code == 503
    assert "sk-" not in error.message


def test_factory_passes_settings_and_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeChat:
        def __init__(self, **kwargs: object) -> None:
            captured["init"] = kwargs

        def with_structured_output(
            self,
            schema: object,
            **kwargs: object,
        ) -> FakeChat:
            captured["schema"] = schema
            captured["structured"] = kwargs
            return self

    monkeypatch.setattr("nexus.ai.client.ChatOpenAI", FakeChat)
    settings = make_settings(
        openai_api_key="sk-test-openai",
        openai_model="gpt-4o-mini",
        ai_timeout_seconds=20,
        ai_max_retries=1,
    )

    OpenAIStructuredModel(settings)

    init = captured["init"]
    assert isinstance(init, dict)
    api_key = init["api_key"]
    assert isinstance(api_key, SecretStr)
    assert api_key.get_secret_value() == "sk-test-openai"
    assert init["model"] == "gpt-4o-mini"
    assert init["timeout"] == 20.0
    assert init["max_retries"] == 1
    assert "temperature" not in init
    assert captured["schema"] is ResearchPlan
    assert captured["structured"] == {"method": "json_schema", "strict": True}
