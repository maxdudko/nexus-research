"""Bounded PDF reading and extraction."""

import io

import pymupdf
import pytest
from starlette.datastructures import Headers, UploadFile

from nexus.ai.errors import ResearchError
from nexus.ingestion.pdf import extract_pdf_pages, read_bounded


def _upload(data: bytes, filename: str = "notes.pdf") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": "application/pdf"}),
    )


def _pdf(pages: list[str], *, encrypted: bool = False) -> bytes:
    document = pymupdf.open()
    try:
        for text in pages:
            document.new_page().insert_text((72, 72), text)
        if encrypted:
            return document.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="owner",
                user_pw="user",
            )
        return document.tobytes()
    finally:
        document.close()


async def test_read_stops_past_the_byte_cap() -> None:
    upload = _upload(b"%PDF" + b"x" * 20)
    with pytest.raises(ResearchError) as exc_info:
        await read_bounded(upload, 8)
    assert exc_info.value.code == "payload_too_large"
    assert await read_bounded(_upload(b"%PDF-1.7"), 16) == b"%PDF-1.7"


def test_extracts_one_based_page_text() -> None:
    pages = extract_pdf_pages(
        _pdf(["First page sentence.", "Second page sentence."]),
        max_pages=10,
        max_chars=10_000,
    )
    assert pages[0][0] == 1
    assert "First page sentence." in pages[0][1]
    assert pages[1][0] == 2
    assert "Second page sentence." in pages[1][1]


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"not a pdf", "unsupported_media"),
        (b"%PDF-1.7 not really", "unsupported_media"),
    ],
)
def test_invalid_pdf_bytes_are_rejected(data: bytes, code: str) -> None:
    with pytest.raises(ResearchError) as exc_info:
        extract_pdf_pages(data, max_pages=10, max_chars=1000)
    assert exc_info.value.code == code


def test_encrypted_pdf_is_rejected() -> None:
    with pytest.raises(ResearchError) as exc_info:
        extract_pdf_pages(
            _pdf(["secret"], encrypted=True),
            max_pages=10,
            max_chars=1000,
        )
    assert exc_info.value.code == "unsupported_media"


def test_blank_pdf_has_no_text() -> None:
    document = pymupdf.open()
    document.new_page()
    data = document.tobytes()
    document.close()
    pages = extract_pdf_pages(data, max_pages=10, max_chars=1000)
    assert pages == [(1, "")]


def test_page_cap_is_rejected_before_a_later_page() -> None:
    with pytest.raises(ResearchError) as exc_info:
        extract_pdf_pages(
            _pdf(["one", "two", "three"]),
            max_pages=2,
            max_chars=1000,
        )
    assert exc_info.value.code == "payload_too_large"
