"""Source migration, uniqueness, and claim recovery against PostgreSQL."""

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from tests.integration.conftest import run_alembic
from tests.integration.scopes import quarantine_scopes

from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.models.source import Source, SourceStatus, SourceType
from nexus.sources.lifecycle import (
    ClaimAction,
    StaleClaim,
    claim_source,
    finish_failed,
    finish_indexed,
    finish_unchanged,
)

pytestmark = pytest.mark.integration

HASH = "ab" * 32
OTHER = "cd" * 32
WEB = "https://example.org/article"


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


async def test_upgrade_is_repeatable_and_matches_the_model(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    repeated = await run_alembic(test_database_url, "upgrade", "head")
    assert repeated.returncode == 0, repeated.stderr
    checked = await run_alembic(test_database_url, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr

    downgraded = await run_alembic(test_database_url, "downgrade", "base")
    assert downgraded.returncode == 0, downgraded.stderr
    await _upgrade(test_database_url)
    checked_again = await run_alembic(test_database_url, "check")
    assert checked_again.returncode == 0, checked_again.stdout + checked_again.stderr

    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            labels = await connection.scalars(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'source_type' "
                    "ORDER BY e.enumsortorder"
                )
            )
            assert list(labels) == ["WEB", "PDF"]
    finally:
        await engine.dispose()


async def test_claims_are_scoped_by_research_and_roll_back(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    first_research = uuid.uuid4()
    second_research = uuid.uuid4()
    await quarantine_scopes(factory, first_research, second_research)
    try:
        async with factory() as session:
            with pytest.raises(RuntimeError, match="boom"):
                async with session.begin():
                    await claim_source(
                        session,
                        research_id=first_research,
                        source_type=SourceType.WEB,
                        uri=WEB,
                    )
                    raise RuntimeError("boom")

        async with factory() as session:
            async with session.begin():
                first = await claim_source(
                    session,
                    research_id=first_research,
                    source_type=SourceType.WEB,
                    uri=WEB,
                    display_name="Article",
                )
                assert first.action is ClaimAction.CLAIMED
                assert first.token is not None
                await finish_indexed(
                    session,
                    source_id=first.source_id,
                    token=first.token,
                    content_hash=HASH,
                    chunk_count=2,
                )
            async with session.begin():
                second = await claim_source(
                    session,
                    research_id=second_research,
                    source_type=SourceType.WEB,
                    uri=WEB,
                )
            assert second.source_id != first.source_id

        async with factory() as session:
            rows = (await session.scalars(select(Source))).all()
        assert len(rows) == 2
        indexed = next(row for row in rows if row.research_id == first_research)
        assert indexed.status is SourceStatus.INDEXED
        assert indexed.content_hash == HASH
        assert indexed.chunk_count == 2
        assert indexed.indexing_token is None
    finally:
        await engine.dispose()


async def test_same_pdf_is_deduplicated_and_a_new_hash_is_another_source(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    await quarantine_scopes(factory, research_id)
    try:
        async with factory() as session:
            async with session.begin():
                created = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.PDF,
                    content_hash=HASH,
                )
                assert created.token is not None
                assert created.uri == f"pdf:{HASH}"
                await finish_indexed(
                    session,
                    source_id=created.source_id,
                    token=created.token,
                    content_hash=HASH,
                    chunk_count=1,
                )
            async with session.begin():
                duplicate = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.PDF,
                    content_hash=HASH,
                )
                replacement = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.PDF,
                    content_hash=OTHER,
                )
        assert duplicate.action is ClaimAction.DEDUPLICATED
        assert duplicate.source_id == created.source_id
        assert duplicate.token is None
        assert replacement.action is ClaimAction.CLAIMED
        assert replacement.source_id != created.source_id
    finally:
        await engine.dispose()


async def test_web_content_change_keeps_the_row_and_the_last_good_hash(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    await quarantine_scopes(factory, research_id)
    try:
        async with factory() as session:
            async with session.begin():
                created = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri=WEB,
                )
                assert created.token is not None
                await finish_indexed(
                    session,
                    source_id=created.source_id,
                    token=created.token,
                    content_hash=HASH,
                    chunk_count=3,
                )
            async with session.begin():
                changed = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri=WEB,
                    content_hash=OTHER,
                )
                assert changed.action is ClaimAction.CLAIMED
                assert changed.source_id == created.source_id
                assert changed.content_hash == HASH
                assert changed.indexing_content_hash == OTHER
                assert changed.token is not None
                await finish_failed(
                    session,
                    source_id=changed.source_id,
                    token=changed.token,
                    error_code="vector_upsert_failed",
                )
            source = await session.get(Source, created.source_id)
        assert source is not None
        assert source.status is SourceStatus.FAILED
        assert source.content_hash == HASH
        assert source.chunk_count == 3
        assert source.indexing_content_hash is None
        assert source.error_code == "vector_upsert_failed"
    finally:
        await engine.dispose()


async def test_failed_retry_and_unchanged_refresh(test_database_url: str) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    await quarantine_scopes(factory, research_id)
    try:
        async with factory() as session:
            async with session.begin():
                created = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri=WEB,
                    content_hash=HASH,
                )
                assert created.token is not None
                await finish_failed(
                    session,
                    source_id=created.source_id,
                    token=created.token,
                    error_code="embedding_failed",
                )
            async with session.begin():
                retried = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri=WEB,
                    content_hash=HASH,
                )
                assert retried.token is not None
                await finish_indexed(
                    session,
                    source_id=retried.source_id,
                    token=retried.token,
                    content_hash=HASH,
                    chunk_count=1,
                )
            async with session.begin():
                refresh = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri=WEB,
                )
                assert refresh.token is not None
                await finish_unchanged(
                    session,
                    source_id=refresh.source_id,
                    token=refresh.token,
                    content_hash=HASH,
                )
            source = await session.get(Source, created.source_id)
        assert source is not None
        assert source.status is SourceStatus.INDEXED
        assert source.error_code is None
        assert source.content_hash == HASH
    finally:
        await engine.dispose()


async def test_replaced_token_cannot_finish(test_database_url: str) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    await quarantine_scopes(factory, research_id)
    try:
        async with factory() as session:
            async with session.begin():
                first = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri=WEB,
                    content_hash=HASH,
                )
            async with session.begin():
                second = await claim_source(
                    session,
                    research_id=research_id,
                    source_type=SourceType.WEB,
                    uri=WEB,
                    content_hash=OTHER,
                )
            assert first.token is not None
            assert second.token is not None
            assert first.token != second.token
            with pytest.raises(StaleClaim):
                async with session.begin():
                    await finish_indexed(
                        session,
                        source_id=first.source_id,
                        token=first.token,
                        content_hash=HASH,
                        chunk_count=1,
                    )
            source = await session.get(Source, first.source_id)
        assert source is not None
        assert source.status is SourceStatus.INDEXING
        assert source.indexing_token == second.token
        assert source.content_hash is None
    finally:
        await engine.dispose()


async def test_direct_duplicate_pdf_identity_is_rejected(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    await quarantine_scopes(factory, research_id)
    try:
        async with factory() as session:
            session.add(
                Source(
                    research_id=research_id,
                    type=SourceType.PDF,
                    uri=f"pdf:{HASH}",
                    content_hash=HASH,
                    status=SourceStatus.INDEXED,
                    chunk_count=1,
                )
            )
            await session.commit()
            session.add(
                Source(
                    research_id=research_id,
                    type=SourceType.PDF,
                    uri=f"pdf:{HASH}",
                    content_hash=HASH,
                    status=SourceStatus.INDEXING,
                    chunk_count=0,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await engine.dispose()


async def test_concurrent_claims_leave_one_row(test_database_url: str) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    factory = session_factory(engine)
    research_id = uuid.uuid4()
    await quarantine_scopes(factory, research_id)

    async def claim_once() -> uuid.UUID:
        async with factory() as session:
            claim = await claim_source(
                session,
                research_id=research_id,
                source_type=SourceType.WEB,
                uri=WEB,
            )
            await session.commit()
            return claim.source_id

    try:
        first, second = await asyncio.gather(claim_once(), claim_once())
        async with factory() as session:
            rows = (await session.scalars(select(Source))).all()
        assert first == second
        assert len(rows) == 1
        assert rows[0].status is SourceStatus.INDEXING
    finally:
        await engine.dispose()
