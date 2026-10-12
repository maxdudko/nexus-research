"""Bounded Qdrant readiness probe."""

import logging

import httpx

logger = logging.getLogger(__name__)


async def qdrant_is_ready(url: str, timeout_seconds: float) -> bool:
    """Return whether Qdrant's readiness endpoint responds with HTTP 200."""
    ready_url = f"{url.rstrip('/')}/readyz"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(ready_url)
    except Exception as exc:
        logger.warning("qdrant readiness check failed: %s", type(exc).__name__)
        return False
    return response.status_code == 200
