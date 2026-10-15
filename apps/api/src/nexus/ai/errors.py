"""Sanitized research failures. Provider text never becomes a response."""

from langchain_core.exceptions import OutputParserException
from langchain_openai.chat_models.base import OpenAIRefusalError
from openai import APITimeoutError, RateLimitError
from pydantic import ValidationError


class ResearchError(Exception):
    """A research failure with a fixed public code and message."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


PROVIDER_NOT_CONFIGURED = "Research provider is not configured."
PROVIDER_TIMEOUT = "Research provider timed out."
PROVIDER_RATE_LIMITED = "Research provider is rate limited."
PROVIDER_UNAVAILABLE = "Research provider is temporarily unavailable."
MODEL_REFUSED = "The research model declined to produce a plan."
INVALID_MODEL_OUTPUT = "The research model returned an invalid plan."


def provider_not_configured() -> ResearchError:
    return ResearchError(
        "provider_not_configured",
        503,
        PROVIDER_NOT_CONFIGURED,
    )


def invalid_model_output() -> ResearchError:
    return ResearchError("invalid_model_output", 503, INVALID_MODEL_OUTPUT)


def map_provider_exception(exc: Exception) -> ResearchError:
    """Map a provider failure to a fixed error. Discard the original text."""
    if isinstance(exc, ResearchError):
        return exc
    if isinstance(exc, OpenAIRefusalError):
        return ResearchError("model_refused", 503, MODEL_REFUSED)
    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return ResearchError("provider_timeout", 504, PROVIDER_TIMEOUT)
    if isinstance(exc, RateLimitError):
        return ResearchError("provider_rate_limited", 429, PROVIDER_RATE_LIMITED)
    if isinstance(exc, (ValidationError, ValueError, OutputParserException)):
        return invalid_model_output()
    return ResearchError("provider_unavailable", 503, PROVIDER_UNAVAILABLE)
