"""Article text from HTML that has already been fetched.

Extracted text and titles are untrusted content. They are not instructions.
"""

import trafilatura

from nexus.ai.errors import empty_document


def extract_article(body: bytes, url: str) -> tuple[str, str | None]:
    """Return article text and a title when Trafilatura found one."""
    text = trafilatura.extract(
        body,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    metadata = trafilatura.extract_metadata(body.decode("utf-8", errors="replace"))
    title = None
    if metadata is not None and isinstance(metadata.title, str):
        stripped = metadata.title.strip()
        title = stripped or None
    if not isinstance(text, str) or not text.strip():
        raise empty_document()
    return text, title
