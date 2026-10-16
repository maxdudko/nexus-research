"""Bounded text extraction for uploaded PDFs.

The original file is not stored. Rebuilding a lost index requires a re-upload.
A Starlette upload spool may exist for the request and is closed by the route.
That path is not the source identity.
"""

import logging
from pathlib import PurePosixPath

import pymupdf  # type: ignore[import-untyped]
from fastapi import UploadFile

from nexus.ai.errors import payload_too_large, unsupported_media

logger = logging.getLogger(__name__)

_READ_CHUNK = 64 * 1024
_PDF_PREFIX = b"%PDF"


async def read_bounded(upload: UploadFile, limit: int) -> bytes:
    """Read an upload in chunks and stop once it passes ``limit``."""
    pieces: list[bytes] = []
    total = 0
    while True:
        piece = await upload.read(_READ_CHUNK)
        if not piece:
            break
        total += len(piece)
        if total > limit:
            logger.warning("pdf rejected: byte limit")
            raise payload_too_large()
        pieces.append(piece)
    return b"".join(pieces)


def extract_pdf_pages(
    data: bytes,
    *,
    max_pages: int,
    max_chars: int,
) -> list[tuple[int, str]]:
    """Return 1-based page text. Encrypted and unreadable files are rejected."""
    if not data.startswith(_PDF_PREFIX):
        raise unsupported_media()
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except RuntimeError, ValueError:
        raise unsupported_media() from None
    try:
        if document.is_encrypted or document.needs_pass:
            raise unsupported_media()
        if document.page_count > max_pages:
            logger.warning("pdf rejected: page limit")
            raise payload_too_large()
        pages: list[tuple[int, str]] = []
        total = 0
        for index in range(document.page_count):
            text = document.load_page(index).get_text("text")
            if not isinstance(text, str):
                text = ""
            total += len(text)
            if total > max_chars:
                logger.warning("pdf rejected: text limit")
                raise payload_too_large()
            pages.append((index + 1, text))
        return pages
    finally:
        document.close()


def filename_display_name(filename: str | None) -> str | None:
    """Use the base filename as a label. It is not the source identity."""
    if filename is None:
        return None
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        return None
    if len(name) > 300:
        return name[:300]
    return name


async def close_upload(upload: UploadFile) -> None:
    """Close the upload spool. Errors during close stay local."""
    try:
        await upload.close()
    except Exception as exc:
        logger.warning("pdf upload close failed: %s", type(exc).__name__)
