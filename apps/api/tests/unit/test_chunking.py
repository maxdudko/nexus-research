"""Normalization, token windows, and content hashes."""

import pytest
from tests.conftest import make_settings

from nexus.ai.errors import ResearchError
from nexus.ingestion.chunking import (
    Chunker,
    normalize_text,
    pdf_content_hash,
    web_content_hash,
)
from nexus.ingestion.pdf import filename_display_name


def test_normalization_is_stable() -> None:
    assert normalize_text("a\r\nb\t  c") == "a\nb c"
    assert normalize_text("a\n\n\n\nb") == "a\n\nb"
    assert normalize_text("e\u0301") == "é"


def test_pdf_hash_uses_page_breaks() -> None:
    same = pdf_content_hash(["a\r\nb", "c"])
    assert same == pdf_content_hash(["a\nb", "c"])
    assert same != pdf_content_hash(["a\nb c"])
    assert web_content_hash("a\nb") == web_content_hash("a\r\nb")
    assert web_content_hash("a\nb") != pdf_content_hash(["a", "b"])


def test_chunks_stay_inside_one_page() -> None:
    chunker = Chunker(
        chunk_tokens=800,
        overlap_tokens=120,
        max_chunks=20,
        model_name="text-embedding-3-small",
    )
    chunks = chunker.chunk_pages(
        [
            (1, "Page one stays here."),
            (2, "   "),
            (3, "Page two stays here."),
        ],
    )

    assert [(chunk.page_number, chunk.chunk_index) for chunk in chunks] == [
        (1, 0),
        (3, 1),
    ]
    assert "Page two" not in chunks[0].text
    assert "Page one" not in chunks[1].text


def test_windows_overlap_by_the_configured_tokens() -> None:
    chunker = Chunker(
        chunk_tokens=10,
        overlap_tokens=4,
        max_chunks=20,
        model_name="text-embedding-3-small",
    )
    text = " ".join(f"word{index}" for index in range(40))
    chunks = chunker.chunk_pages([(1, text)])

    assert len(chunks) >= 3
    assert chunks[0].page_number == 1
    assert chunks[0].text.endswith("word3 word4")
    assert chunks[1].text.startswith("word3 word4")
    assert chunks[1].text.endswith("word6 word7")
    assert chunks[2].text.startswith("word6 word7")


def test_chunk_cap_is_a_payload_limit() -> None:
    chunker = Chunker(
        chunk_tokens=4,
        overlap_tokens=0,
        max_chunks=1,
        model_name="text-embedding-3-small",
    )
    text = " ".join(f"word{index}" for index in range(30))
    with pytest.raises(ResearchError) as exc_info:
        chunker.chunk_pages([(1, text)])
    assert exc_info.value.code == "payload_too_large"


def test_empty_text_has_no_chunks() -> None:
    chunker = Chunker(
        chunk_tokens=make_settings().chunk_tokens,
        overlap_tokens=make_settings().chunk_overlap_tokens,
        max_chunks=10,
        model_name="text-embedding-3-small",
    )
    with pytest.raises(ResearchError) as exc_info:
        chunker.chunk_pages([(1, "   \n")])
    assert exc_info.value.code == "empty_document"


def test_filename_keeps_only_the_base_name() -> None:
    assert filename_display_name("../secret/notes.pdf") == "notes.pdf"
    assert filename_display_name(None) is None
    assert filename_display_name("..") is None
