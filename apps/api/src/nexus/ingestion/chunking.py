"""Deterministic normalization and token windows.

Chunk size is part of the collection contract. Changing ``chunk_tokens``,
``chunk_overlap_tokens``, or the point-id formula requires a new
``NEXUS_RAG_COLLECTION``. An indexed source is deduplicated and is not rebuilt
in place.
"""

import hashlib
import logging
import os
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import tiktoken

from nexus.ai.errors import empty_document, payload_too_large

logger = logging.getLogger(__name__)

# The cl100k_base ranks live beside this module so chunking does not fetch them.
_TOKEN_CACHE = Path(__file__).resolve().parent / "data"
_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class TextChunk:
    """One passage that stays inside a single PDF page or web article."""

    chunk_index: int
    text: str
    page_number: int | None


def normalize_text(value: str) -> str:
    """Return stable text for hashing and chunking."""
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def pdf_content_hash(pages: Sequence[str]) -> str:
    """Hash normalized PDF pages. The original file bytes are not the identity."""
    payload = "\f".join(normalize_text(page) for page in pages)
    return hashlib.sha256(payload.encode()).hexdigest()


def web_content_hash(text: str) -> str:
    """Hash normalized article text."""
    return hashlib.sha256(normalize_text(text).encode()).hexdigest()


class Chunker:
    """Split normalized text with the embedding model's tokenizer."""

    def __init__(
        self,
        *,
        chunk_tokens: int,
        overlap_tokens: int,
        max_chunks: int,
        model_name: str,
    ) -> None:
        if overlap_tokens >= chunk_tokens:
            raise ValueError("chunk overlap must be smaller than the chunk size")
        self._chunk_tokens = chunk_tokens
        self._overlap_tokens = overlap_tokens
        self._max_chunks = max_chunks
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(_TOKEN_CACHE))
        self._encoding = tiktoken.encoding_for_model(model_name)

    def chunk_pages(self, pages: Sequence[tuple[int | None, str]]) -> list[TextChunk]:
        """Chunk each page separately. ``chunk_index`` increases across pages."""
        chunks: list[TextChunk] = []
        for page_number, text in pages:
            normalized = normalize_text(text)
            if not normalized:
                continue
            for window in self._windows(normalized):
                chunks.append(
                    TextChunk(
                        chunk_index=len(chunks),
                        text=window,
                        page_number=page_number,
                    ),
                )
                if len(chunks) > self._max_chunks:
                    logger.warning("ingestion rejected: chunk limit")
                    raise payload_too_large()
        if not chunks:
            raise empty_document()
        return chunks

    def _windows(self, text: str) -> list[str]:
        tokens = self._encoding.encode(text)
        if not tokens:
            return []
        size = self._chunk_tokens
        step = size - self._overlap_tokens
        windows: list[str] = []
        start = 0
        while start < len(tokens):
            piece = self._encoding.decode(tokens[start : start + size]).strip()
            if piece:
                windows.append(piece)
            if start + size >= len(tokens):
                break
            start += step
        return windows
