"""Domain history for a research run. Checkpoint tables stay internal."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    Uuid,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexus.models.base import Base

RUN_STATUSES = (
    "running",
    "completed",
    "partial",
    "insufficient_evidence",
    "failed",
    "interrupted",
)
SUCCESS_STATUSES = ("completed", "partial", "insufficient_evidence")
FAILURE_STATUSES = ("failed", "interrupted")

_STATUS_LIST = ", ".join(f"'{status}'" for status in RUN_STATUSES)
_SUCCESS_LIST = ", ".join(f"'{status}'" for status in SUCCESS_STATUSES)
_FAILURE_LIST = ", ".join(f"'{status}'" for status in FAILURE_STATUSES)


class ResearchRun(Base):
    """One request-scoped execution. ``research_id`` is an evidence scope."""

    __tablename__ = "research_runs"
    __table_args__ = (
        Index(
            "ix_research_runs_created_at_run_id",
            desc("created_at"),
            desc("run_id"),
        ),
        Index(
            "ix_research_runs_research_id_created_at",
            "research_id",
            desc("created_at"),
        ),
        CheckConstraint(
            f"status IN ({_STATUS_LIST})",
            name="ck_research_runs_status",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'",
            name="ck_research_runs_error_code",
        ),
        CheckConstraint(
            "char_length(question) BETWEEN 10 AND 2000",
            name="ck_research_runs_question_length",
        ),
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND error_code IS NULL) "
            f"OR (status IN ({_SUCCESS_LIST}) "
            "AND finished_at IS NOT NULL AND error_code IS NULL) "
            f"OR (status IN ({_FAILURE_LIST}) "
            "AND finished_at IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_research_runs_terminal",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    research_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    usage: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ResearchReportRecord(Base):
    """The validated report for a terminal success. Failures have no row."""

    __tablename__ = "research_reports"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("research_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    report: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
