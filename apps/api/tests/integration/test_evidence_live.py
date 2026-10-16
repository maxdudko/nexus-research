"""Opt-in embedding and public-page smoke tests. Default pytest skips them."""

import math
import os

import pytest
from tests.conftest import make_settings

from nexus.ai.errors import ResearchError
from nexus.ingestion.transport import fetch_public_page
from nexus.retrieval.embeddings import OpenAIEmbedder

pytestmark = [pytest.mark.live, pytest.mark.integration]


async def test_live_embedding_returns_a_finite_vector() -> None:
    if os.environ.get("NEXUS_RUN_LIVE_AI") != "1":
        pytest.skip("set NEXUS_RUN_LIVE_AI=1 to run paid provider smoke tests")
    key = os.environ.get("NEXUS_OPENAI_API_KEY")
    if not key:
        pytest.skip("NEXUS_OPENAI_API_KEY is required")

    settings = make_settings(openai_api_key=key)
    vector = await OpenAIEmbedder(settings).embed_query(
        "What limits AI coding agents?",
    )

    assert len(vector) == settings.rag_vector_size
    assert all(math.isfinite(value) for value in vector)


async def test_live_public_page_fetch() -> None:
    if os.environ.get("NEXUS_RUN_LIVE_WEB") != "1":
        pytest.skip("set NEXUS_RUN_LIVE_WEB=1 to fetch a public page")
    url = os.environ.get("NEXUS_LIVE_WEB_URL", "").strip()
    if not url:
        pytest.skip("NEXUS_LIVE_WEB_URL is required")

    settings = make_settings()
    try:
        page = await fetch_public_page(
            url,
            max_bytes=settings.web_max_bytes,
            max_redirects=settings.web_max_redirects,
            timeout_seconds=settings.web_timeout_seconds,
        )
    except ResearchError as exc:
        assert url not in exc.message
        return

    assert page.body
    assert page.url.startswith(("http://", "https://"))
