"""Async Tavily search adapter. The client is built only when a search runs."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
from nexus.observability.spans import operation
from nexus.schemas.research import QUERY_MAX_LENGTH, SearchResult

MAX_RESULTS = 5
_RETRY_DELAY_SECONDS = 0.2
_TRANSIENT_ERRORS = (TavilyTimeoutError, httpx.TimeoutException, httpx.NetworkError)
SearchFn = Callable[[str, int], Awaitable[object]]


@dataclass(frozen=True)
class SearchOptions:
    search_depth: Literal["basic", "advanced"] = "basic"
    time_range: Literal["day", "week", "month", "year"] | None = None
    include_domains: tuple[str, ...] = ()


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
        options: SearchOptions | None = None,
    ) -> list[SearchResult]:
        stripped = query.strip()
        if not stripped:
            raise ValueError("query must not be empty")
        if len(stripped) > QUERY_MAX_LENGTH:
            raise ValueError("query must be at most 200 characters")
        if not 1 <= max_results <= MAX_RESULTS:
            raise ValueError("max_results must be from 1 to 5")
        payload = await self._fetch(stripped, max_results, options)
        try:
            results = normalize_results(payload, max_results=max_results)
            now = datetime.now(UTC)
            return [
                r.model_copy(update={"query": stripped, "retrieved_at": now})
                for r in results
            ]
        except MalformedSearchResponse:
            raise search_failed() from None

    async def _fetch(
        self, query: str, max_results: int, options: SearchOptions | None = None
    ) -> object:
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
                    search_depth=(options or SearchOptions()).search_depth,
                    time_range=options.time_range if options else None,
                    include_domains=list(options.include_domains) if options else None,
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
            retry = False
            try:
                with operation(operation="web_search", provider="tavily") as handle:
                    handle.physical_attempts = 1
                    try:
                        return await call()
                    except TimeoutError:
                        raise
                    except _TRANSIENT_ERRORS as exc:
                        mapped = _map_search_error(exc)
                        handle.outcome = "error"
                        handle.error_code = mapped.code
                        if attempt + 1 >= attempts:
                            raise mapped from None
                        retry = True
                    except Exception as exc:
                        raise _map_search_error(exc) from None
            except TimeoutError:
                raise
            if retry:
                await asyncio.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
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
                relevance_score=_score(item.get("score")),
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
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_")
            and k.lower() not in {"fbclid", "gclid", "msclkid"}
        )
    )
    return urlunsplit((scheme, netloc, parts.path, query, ""))


def _score(value: object) -> float:
    import math

    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        return max(0.0, min(1.0, float(value)))
    return 0.0


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
