"""Domain research history against PostgreSQL. Checkpoint tables are not read."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select, text
from tests.conftest import client_for, make_settings
from tests.integration.conftest import run_alembic

from nexus.ai.errors import ResearchError
from nexus.infrastructure.database import create_db_engine, session_factory
from nexus.main import create_app
from nexus.models.run import ResearchReportRecord
from nexus.research.history import HistoryConflict, ResearchHistory
from nexus.schemas.report import EvidenceRef, GraphUsage, ReportSection, ResearchReport

pytestmark = pytest.mark.integration

SOURCE = "11111111-1111-4111-8111-111111111111"
CHUNK = "22222222-2222-4222-8222-222222222222"
QUESTION = "How do bounded research graphs stop?"
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _usage() -> GraphUsage:
    return GraphUsage(
        iterations=1,
        search_calls=1,
        llm_calls=1,
        pages_indexed=0,
        pages_failed=0,
    )


def _report(
    status: str,
    summary: str | None = None,
) -> ResearchReport:
    if status == "insufficient_evidence":
        return ResearchReport(
            title="Limits",
            summary=summary or "Not enough evidence was found for this question today.",
            sections=[],
            limitations=["No accepted sources."],
            status="insufficient_evidence",
            sources=[],
        )
    return ResearchReport(
        title="Limits",
        summary=summary or "Bounded graphs stop on a deadline.",
        sections=[
            ReportSection(
                heading="Stop",
                content="The deadline ends the run.",
                citations=["E1"],
            )
        ],
        limitations=[],
        status=status,  # type: ignore[arg-type]
        sources=[
            EvidenceRef(
                evidence_id="E1",
                source_id=SOURCE,
                chunk_id=CHUNK,
                research_id="33333333-3333-4333-8333-333333333333",
                url="https://example.org/notes",
            )
        ],
    )


async def _upgrade(database_url: str) -> None:
    completed = await run_alembic(database_url, "upgrade", "head")
    assert completed.returncode == 0, completed.stderr


async def test_research_revision_matches_the_model_and_keeps_sources(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    repeated = await run_alembic(test_database_url, "upgrade", "head")
    assert repeated.returncode == 0, repeated.stderr
    checked = await run_alembic(test_database_url, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr

    downgraded = await run_alembic(test_database_url, "downgrade", "6f509f423779")
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_db_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            names = set(
                await connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
    finally:
        await engine.dispose()
    assert "sources" in names
    assert "research_runs" not in names
    assert "research_reports" not in names

    await _upgrade(test_database_url)
    checked_again = await run_alembic(test_database_url, "check")
    assert checked_again.returncode == 0, checked_again.stdout + checked_again.stderr


async def test_success_failure_and_conflict_transitions(test_database_url: str) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    history = ResearchHistory(session_factory(engine))
    research_id = uuid.uuid4()
    try:
        for status in ("completed", "partial", "insufficient_evidence"):
            run_id = uuid.uuid4()
            await history.create_running(run_id, research_id, QUESTION, NOW)
            await history.finalize_success(
                run_id,
                _report(status),
                ["budget_exhausted", "not a code"],
                _usage(),
                NOW + timedelta(seconds=5),
            )
            await history.finalize_success(
                run_id,
                _report(status),
                ["budget_exhausted"],
                _usage(),
                NOW + timedelta(seconds=9),
            )
            stored = await history.get_run(run_id)
            assert stored is not None
            assert stored.run.status == status
            assert stored.run.finished_at == NOW + timedelta(seconds=5)
            assert stored.run.warnings == ["budget_exhausted"]
            assert stored.report is not None
            assert stored.report.status == status
            with pytest.raises(HistoryConflict):
                await history.finalize_success(
                    run_id,
                    _report(status, summary="A different saved summary for this run."),
                    [],
                    _usage(),
                    NOW,
                )
            with pytest.raises(HistoryConflict):
                await history.finalize_failure(
                    run_id,
                    "provider_unavailable",
                    interrupted=False,
                    now=NOW,
                )
            again = await history.get_run(run_id)
            assert again is not None
            assert again.run.status == status
            assert again.report == stored.report

        failed_id = uuid.uuid4()
        await history.create_running(failed_id, research_id, QUESTION, NOW)
        await history.finalize_failure(
            failed_id,
            "provider exploded: secret",
            interrupted=False,
            now=NOW + timedelta(seconds=1),
        )
        await history.finalize_failure(
            failed_id,
            "provider_unavailable",
            interrupted=False,
            now=NOW + timedelta(seconds=2),
        )
        failed = await history.get_run(failed_id)
        assert failed is not None
        assert failed.run.status == "failed"
        assert failed.run.error_code == "provider_unavailable"
        assert failed.report is None
        with pytest.raises(HistoryConflict):
            await history.finalize_failure(
                failed_id,
                "provider_timeout",
                interrupted=False,
                now=NOW,
            )

        stopped_id = uuid.uuid4()
        await history.create_running(stopped_id, research_id, QUESTION, NOW)
        await history.finalize_failure(
            stopped_id,
            "deadline_exceeded",
            interrupted=True,
            now=NOW + timedelta(seconds=1),
        )
        stopped = await history.get_run(stopped_id)
        assert stopped is not None
        assert stopped.run.status == "interrupted"
        assert stopped.run.error_code == "deadline_exceeded"
        assert stopped.report is None

        stale_id = uuid.uuid4()
        await history.create_running(stale_id, research_id, QUESTION, NOW)
        stale = await history.get_run(stale_id)
        assert stale is not None
        assert stale.run.status == "running"
        assert stale.report is None
        assert stale.run.finished_at is None
    finally:
        await engine.dispose()


async def test_report_write_failure_rolls_back_the_status(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    history = ResearchHistory(session_factory(engine))
    run_id = uuid.uuid4()
    try:
        await history.create_running(run_id, uuid.uuid4(), QUESTION, NOW)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE OR REPLACE FUNCTION nexus_fail_report() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN RAISE EXCEPTION 'report write failed'; END; $$"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER nexus_fail_report "
                    "BEFORE INSERT ON research_reports "
                    "FOR EACH ROW EXECUTE FUNCTION nexus_fail_report()"
                )
            )
        with pytest.raises(Exception, match="report write failed"):
            await history.finalize_success(
                run_id,
                _report("completed"),
                [],
                _usage(),
                NOW + timedelta(seconds=1),
            )
        stored = await history.get_run(run_id)
        assert stored is not None
        assert stored.run.status == "running"
        assert stored.report is None
        async with session_factory(engine)() as session:
            count = await session.scalar(
                select(func.count()).select_from(ResearchReportRecord)
            )
        assert count == 0
    finally:
        await engine.dispose()


async def test_keyset_pages_have_no_duplicates_or_gaps(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    history = ResearchHistory(session_factory(engine))
    research_id = uuid.uuid4()
    same_time = NOW + timedelta(minutes=5)
    low = uuid.UUID("00000000-0000-4000-8000-000000000001")
    high = uuid.UUID("00000000-0000-4000-8000-000000000002")
    try:
        await history.create_running(low, research_id, QUESTION, same_time)
        await history.create_running(high, research_id, QUESTION, same_time)
        earlier = uuid.uuid4()
        await history.create_running(
            earlier,
            research_id,
            QUESTION,
            NOW,
        )
        first = await history.list_runs(2, None)
        assert [item.run_id for item in first.items] == [high, low]
        assert first.next_cursor is not None
        second = await history.list_runs(2, first.next_cursor)
        assert [item.run_id for item in second.items] == [earlier]
        assert second.next_cursor is None
        seen = [item.run_id for item in first.items + second.items]
        assert len(seen) == len(set(seen)) == 3
    finally:
        await engine.dispose()


async def test_read_api_hides_reports_and_rejects_a_bad_cursor(
    test_database_url: str,
) -> None:
    await _upgrade(test_database_url)
    engine = create_db_engine(test_database_url)
    history = ResearchHistory(session_factory(engine))
    run_id = uuid.uuid4()
    statements: list[str] = []

    def capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    app = create_app(make_settings(database_url=test_database_url))
    try:
        await history.create_running(run_id, uuid.uuid4(), QUESTION, NOW)
        await history.finalize_success(
            run_id,
            _report("completed"),
            [],
            _usage(),
            NOW + timedelta(seconds=1),
        )
        async with session_factory(engine)() as session:
            async with session.begin():
                record = await session.get(ResearchReportRecord, run_id)
                assert record is not None
                record.report = {"title": "broken"}
        statements.clear()
        listed = await history.list_runs(20, None)
        assert listed.items[0].run_id == run_id
        assert all("research_reports" not in statement for statement in statements)
        assert all("checkpoint" not in statement.lower() for statement in statements)
        with pytest.raises(ResearchError) as corrupt:
            await history.get_run(run_id)
        assert corrupt.value.code == "invalid_state"
        detail_sql = [item for item in statements if "research_reports" in item]
        assert detail_sql
        assert all("checkpoint" not in item.lower() for item in statements)

        async with client_for(app) as client:
            missing = await client.get(f"/api/v1/research/runs/{uuid.uuid4()}")
            bad_cursor = await client.get(
                "/api/v1/research/runs",
                params={"cursor": "not-a-cursor"},
            )
            bad_id = await client.get("/api/v1/research/runs/not-a-uuid")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        assert bad_cursor.status_code == 422
        assert bad_cursor.json()["error"]["code"] == "invalid_request"
        assert bad_id.status_code == 422
        assert bad_id.json()["error"]["code"] == "invalid_request"
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        await engine.dispose()
