"""Source identity decisions without a database."""

import uuid

import pytest

from nexus.models.source import SourceStatus, SourceType
from nexus.sources.lifecycle import (
    ClaimAction,
    StaleClaim,
    normalize_claim,
    plan_claim,
    plan_finish_failed,
    plan_finish_indexed,
    plan_finish_unchanged,
)

HASH = "ab" * 32
OTHER = "cd" * 32
TOKEN = uuid.uuid4()
OTHER_TOKEN = uuid.uuid4()


def test_new_pdf_uses_the_hash_as_its_internal_uri() -> None:
    research_id = uuid.uuid4()
    request = normalize_claim(
        research_id=research_id,
        source_type=SourceType.PDF,
        uri=None,
        content_hash=HASH,
        display_name="  Paper  ",
    )
    assert request.uri == f"pdf:{HASH}"
    assert request.display_name == "Paper"
    action = plan_claim(status=None, content_hash=None, incoming_hash=HASH)
    assert action is ClaimAction.CLAIMED


def test_new_web_row_keeps_the_supplied_uri() -> None:
    request = normalize_claim(
        research_id=uuid.uuid4(),
        source_type=SourceType.WEB,
        uri="  https://example.org/a  ",
        content_hash=None,
        display_name="   ",
    )
    assert request.uri == "https://example.org/a"
    assert request.content_hash is None
    assert request.display_name is None


def test_pdf_without_a_hash_is_rejected() -> None:
    with pytest.raises(ValueError, match="content_hash"):
        normalize_claim(
            research_id=uuid.uuid4(),
            source_type=SourceType.PDF,
            uri=None,
            content_hash=None,
            display_name=None,
        )


def test_web_uri_cannot_use_the_pdf_prefix() -> None:
    with pytest.raises(ValueError, match="pdf:"):
        normalize_claim(
            research_id=uuid.uuid4(),
            source_type=SourceType.WEB,
            uri="pdf:abcdef",
            content_hash=None,
            display_name=None,
        )


def test_indexed_source_with_the_same_hash_is_deduplicated() -> None:
    action = plan_claim(
        status=SourceStatus.INDEXED,
        content_hash=HASH,
        incoming_hash=HASH,
    )
    assert action is ClaimAction.DEDUPLICATED


def test_changed_web_hash_is_claimed_again() -> None:
    action = plan_claim(
        status=SourceStatus.INDEXED,
        content_hash=HASH,
        incoming_hash=OTHER,
    )
    assert action is ClaimAction.CLAIMED


def test_failed_and_indexing_rows_are_reclaimed() -> None:
    for status in (SourceStatus.FAILED, SourceStatus.INDEXING, SourceStatus.PENDING):
        action = plan_claim(status=status, content_hash=HASH, incoming_hash=HASH)
        assert action is ClaimAction.CLAIMED


def test_finish_rejects_a_replaced_token() -> None:
    with pytest.raises(StaleClaim):
        plan_finish_indexed(
            status=SourceStatus.INDEXING,
            owner_token=TOKEN,
            token=OTHER_TOKEN,
            indexing_content_hash=HASH,
            content_hash=HASH,
            chunk_count=1,
        )


def test_finish_rejects_a_different_attempt_hash_and_a_negative_count() -> None:
    with pytest.raises(ValueError, match="claim"):
        plan_finish_indexed(
            status=SourceStatus.INDEXING,
            owner_token=TOKEN,
            token=TOKEN,
            indexing_content_hash=HASH,
            content_hash=OTHER,
            chunk_count=1,
        )
    with pytest.raises(ValueError, match="chunk_count"):
        plan_finish_indexed(
            status=SourceStatus.INDEXING,
            owner_token=TOKEN,
            token=TOKEN,
            indexing_content_hash=None,
            content_hash=HASH,
            chunk_count=-1,
        )


def test_failed_finish_keeps_a_stable_error_token() -> None:
    assert (
        plan_finish_failed(
            status=SourceStatus.INDEXING,
            owner_token=TOKEN,
            token=TOKEN,
            error_code="vector_upsert_failed",
        )
        == "vector_upsert_failed"
    )
    with pytest.raises(ValueError, match="error_code"):
        plan_finish_failed(
            status=SourceStatus.INDEXING,
            owner_token=TOKEN,
            token=TOKEN,
            error_code="Qdrant said no",
        )


def test_unchanged_finish_requires_the_indexed_hash() -> None:
    plan_finish_unchanged(
        status=SourceStatus.INDEXING,
        owner_token=TOKEN,
        token=TOKEN,
        content_hash=HASH,
        fetched_hash=HASH,
    )
    with pytest.raises(ValueError, match="indexed content"):
        plan_finish_unchanged(
            status=SourceStatus.INDEXING,
            owner_token=TOKEN,
            token=TOKEN,
            content_hash=HASH,
            fetched_hash=OTHER,
        )
