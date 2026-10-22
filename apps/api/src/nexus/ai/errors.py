"""Sanitized research failures. Provider text never becomes a response."""

from langchain_core.exceptions import OutputParserException
from langchain_openai.chat_models.base import OpenAIRefusalError
from openai import APITimeoutError, RateLimitError
from pydantic import ValidationError


class ResearchError(Exception):
    """A research failure with a fixed public code and message."""

    def __init__(
        self,
        code: str,
        status_code: int,
        message: str,
        *,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after


PROVIDER_NOT_CONFIGURED = "Research provider is not configured."
PROVIDER_TIMEOUT = "Research provider timed out."
PROVIDER_RATE_LIMITED = "Research provider is rate limited."
PROVIDER_UNAVAILABLE = "Research provider is temporarily unavailable."
MODEL_REFUSED = "The research model declined the request."
INVALID_MODEL_OUTPUT = "The research model returned invalid output."


def provider_not_configured() -> ResearchError:
    return ResearchError(
        "provider_not_configured",
        503,
        PROVIDER_NOT_CONFIGURED,
    )


def invalid_model_output() -> ResearchError:
    return ResearchError("invalid_model_output", 503, INVALID_MODEL_OUTPUT)


def provider_timeout() -> ResearchError:
    return ResearchError("provider_timeout", 504, PROVIDER_TIMEOUT)


def provider_rate_limited() -> ResearchError:
    return ResearchError("provider_rate_limited", 429, PROVIDER_RATE_LIMITED)


def provider_unavailable() -> ResearchError:
    return ResearchError("provider_unavailable", 503, PROVIDER_UNAVAILABLE)


def payload_too_large() -> ResearchError:
    return ResearchError(
        "payload_too_large",
        413,
        "The document exceeds the ingestion limit.",
    )


def unsupported_media() -> ResearchError:
    return ResearchError(
        "unsupported_media",
        415,
        "Only a text PDF can be indexed.",
    )


def empty_document() -> ResearchError:
    return ResearchError(
        "empty_document",
        422,
        "The document has no extractable text.",
    )


def unsafe_url() -> ResearchError:
    return ResearchError(
        "unsafe_url",
        422,
        "The URL is not a public HTTP(S) address.",
    )


def indexing_in_progress() -> ResearchError:
    return ResearchError(
        "indexing_in_progress",
        409,
        "Another indexing attempt is still running.",
    )


def fetch_failed() -> ResearchError:
    return ResearchError("fetch_failed", 422, "The page could not be fetched.")


def fetch_timeout() -> ResearchError:
    return ResearchError("fetch_timeout", 504, "The page fetch timed out.")


def ingestion_timeout() -> ResearchError:
    return ResearchError("ingestion_timeout", 504, "Ingestion timed out.")


def collection_mismatch() -> ResearchError:
    """The Qdrant collection exists but does not match this index contract."""
    return ResearchError(
        "collection_mismatch",
        503,
        "The evidence index configuration does not match this service.",
    )


def search_failed() -> ResearchError:
    return ResearchError(
        "search_failed",
        503,
        "Web search is temporarily unavailable.",
    )


def checkpoint_unavailable() -> ResearchError:
    return ResearchError(
        "checkpoint_unavailable",
        503,
        "The research checkpoint store is unavailable.",
    )


def deadline_exceeded() -> ResearchError:
    return ResearchError(
        "deadline_exceeded",
        504,
        "The research run exceeded its deadline.",
    )


def run_interrupted() -> ResearchError:
    return ResearchError(
        "interrupted",
        504,
        "The research run was interrupted.",
    )


def invalid_state() -> ResearchError:
    return ResearchError(
        "invalid_state",
        500,
        "The research run state is invalid.",
    )


def not_found() -> ResearchError:
    return ResearchError(
        "not_found",
        404,
        "The research run was not found.",
    )


def invalid_credentials() -> ResearchError:
    return ResearchError(
        "invalid_credentials",
        401,
        "The email or password is incorrect.",
    )


def unauthenticated() -> ResearchError:
    return ResearchError(
        "unauthenticated",
        401,
        "Authentication is required.",
    )


def forbidden() -> ResearchError:
    return ResearchError(
        "forbidden",
        403,
        "The request was rejected.",
    )


def invalid_request(field: str) -> ResearchError:
    return ResearchError("invalid_request", 422, f"{field}: invalid value")


def history_unavailable() -> ResearchError:
    return ResearchError(
        "history_unavailable",
        503,
        "Research history is temporarily unavailable.",
    )


def too_many_requests(
    *,
    retry_after: int | None = None,
    message: str | None = None,
) -> ResearchError:
    return ResearchError(
        "too_many_requests",
        429,
        message or "Too many research runs are in progress.",
        retry_after=retry_after,
    )


def map_provider_exception(exc: Exception) -> ResearchError:
    """Map a provider failure to a fixed error. Discard the original text."""
    if isinstance(exc, ResearchError):
        return exc
    if isinstance(exc, OpenAIRefusalError):
        return ResearchError("model_refused", 503, MODEL_REFUSED)
    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return provider_timeout()
    if isinstance(exc, RateLimitError):
        return provider_rate_limited()
    if isinstance(exc, (ValidationError, ValueError, OutputParserException)):
        return invalid_model_output()
    return provider_unavailable()
