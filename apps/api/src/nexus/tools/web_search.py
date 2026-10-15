"""Async Tavily search adapter. The client is built only when a search runs."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
from tavily import AsyncTavilyClient  # type: ignore[import-untyped]
from tavily.errors import (  # type: ignore[import-untyped]
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    UsageLimitExceededError,
)
from tavily.errors import (
    TimeoutError as TavilyTimeoutError,
)

from nexus.ai.errors import (
    ResearchError,
    provider_not_configured,
    provider_rate_limited,
    provider_timeout,
    provider_unavailable,
    search_failed,
)
from nexus.core.config import Settings
from nexus.schemas.research import QUERY_MAX_LENGTH, SearchResult

MAX_RESULTS = 5
_RETRY_DELAY_SECONDS = 0.2
_TRANSIENT_ERRORS = (TavilyTimeoutError, httpx.TimeoutException, httpx.NetworkError)
SearchFn = Callable[[str, int], Awaitable[object]]


class WebSearch:
    """Normalize Tavily search results. Tests can inject the provider call."""

    def __init__(self, settings: Settings, search_fn: SearchFn | None = None) -> None:
        self._settings = settings
        self._search_fn = search_fn

    async def search(
        self,
        query: str,
        *,
        max_results: int = MAX_RESULTS,
    ) -> list[SearchResult]:
        stripped = query.strip()
        if not stripped:
            raise ValueError("query must not be empty")
        if len(stripped) > QUERY_MAX_LENGTH:
            raise ValueError("query must be at most 200 characters")
        if not 1 <= max_results <= MAX_RESULTS:
            raise ValueError("max_results must be from 1 to 5")
        payload = await self._fetch(stripped, max_results)
        try:
            return normalize_results(payload, max_results=max_results)
        except MalformedSearchResponse:
            raise search_failed() from None

    async def _fetch(self, query: str, max_results: int) -> object:
        search_fn = self._search_fn
        if search_fn is not None:
            return await self._call_with_retries(lambda: search_fn(query, max_results))
        key = self._settings.tavily_api_key
        if key is None:
            raise provider_not_configured()
        secret = key.get_secret_value()

        async def call() -> object:
            async with AsyncTavilyClient(api_key=secret) as client:
                return await client.search(
                    query,
                    max_results=max_results,
                    include_answer=False,
                    include_raw_content=False,
                    timeout=self._settings.ai_timeout_seconds,
                )

        return await self._call_with_retries(call)

    async def _call_with_retries(
        self,
        call: Callable[[], Awaitable[object]],
    ) -> object:
        attempts = self._settings.ai_max_retries + 1
        for attempt in range(attempts):
            try:
                return await call()
            except TimeoutError:
                raise
            except _TRANSIENT_ERRORS as exc:
                if attempt + 1 >= attempts:
                    raise _map_search_error(exc) from None
                await asyncio.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
            except Exception as exc:
                raise _map_search_error(exc) from None
        raise search_failed()


class MalformedSearchResponse(Exception):
    """The provider payload is not a result list. An empty list is valid."""


def normalize_results(payload: object, *, max_results: int) -> list[SearchResult]:
    """Turn a Tavily response dict into deduplicated search results."""
    if not isinstance(payload, dict) or "results" not in payload:
        raise MalformedSearchResponse
    raw_results = payload["results"]
    if not isinstance(raw_results, list):
        raise MalformedSearchResponse
    seen: set[str] = set()
    results: list[SearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("url")
        if not isinstance(raw_url, str):
            continue
        url = normalize_url(raw_url)
        if url is None or url in seen:
            continue
        seen.add(url)
        title = item.get("title")
        content = item.get("content")
        results.append(
            SearchResult(
                title=title if isinstance(title, str) else "",
                url=url,
                snippet=content if isinstance(content, str) else "",
                published_at=_published_at(item.get("published_date")),
            )
        )
    return results[:max_results]


def normalize_url(value: str) -> str | None:
    """Return a comparable http(s) URL, or None when the URL is unusable."""
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not host:
        return None
    try:
        normalized_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    if port is None or default_port:
        netloc = normalized_host
    else:
        netloc = f"{normalized_host}:{port}"
    return urlunsplit((scheme, netloc, parts.path, parts.query, ""))


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _map_search_error(exc: Exception) -> ResearchError:
    if isinstance(exc, ResearchError):
        return exc
    if isinstance(exc, (TavilyTimeoutError, httpx.TimeoutException)):
        return provider_timeout()
    if isinstance(exc, UsageLimitExceededError):
        return provider_rate_limited()
    if isinstance(
        exc,
        (InvalidAPIKeyError, MissingAPIKeyError, ForbiddenError, BadRequestError),
    ):
        return provider_unavailable()
    if isinstance(exc, httpx.NetworkError):
        return search_failed()
    return search_failed()
