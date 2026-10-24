"""Immutable evaluation history. These rows are not user research."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    Uuid,
    desc,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexus.models.base import Base

EVALUATION_SUITES = ("offline", "live")
EVALUATION_OUTCOMES = ("passed", "failed", "informational")
PROVENANCE_LEVELS = ("offline_recomputed", "operator_imported")
CASE_CATEGORIES = ("retrieval", "citation", "grounding")


class EvaluationRun(Base):
    """One imported evaluation artifact. Rows are inserted, not updated."""

    __tablename__ = "evaluation_runs"
    __table_args__ = (
        Index(
            "ix_evaluation_runs_imported_at_id",
            desc("imported_at"),
            desc("id"),
        ),
        CheckConstraint(
            "suite IN ('offline', 'live')",
            name="ck_evaluation_runs_suite",
        ),
        CheckConstraint(
            "outcome IN ('passed', 'failed', 'informational')",
            name="ck_evaluation_runs_outcome",
        ),
        CheckConstraint(
            "(suite = 'offline' AND outcome IN ('passed', 'failed')) "
            "OR (suite = 'live' AND outcome = 'informational')",
            name="ck_evaluation_runs_suite_outcome",
        ),
        CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_runs_artifact_sha256",
        ),
        CheckConstraint(
            "dataset_version ~ '^[A-Za-z0-9._-]{1,64}$'",
            name="ck_evaluation_runs_dataset_version",
        ),
        CheckConstraint(
            "dataset_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_runs_dataset_sha256",
        ),
        CheckConstraint(
            "report_version = '1'",
            name="ck_evaluation_runs_report_version",
        ),
        CheckConstraint(
            "scorer_version = '1'",
            name="ck_evaluation_runs_scorer_version",
        ),
        CheckConstraint(
            "k >= 1 AND k <= 100",
            name="ck_evaluation_runs_k",
        ),
        CheckConstraint(
            "code_revision IS NULL OR code_revision ~ '^[0-9a-f]{40}$'",
            name="ck_evaluation_runs_code_revision",
        ),
        CheckConstraint(
            "code_revision_source IS NULL "
            "OR code_revision_source = 'operator_asserted'",
            name="ck_evaluation_runs_code_revision_source",
        ),
        CheckConstraint(
            "(code_revision IS NULL AND code_revision_source IS NULL) "
            "OR (code_revision IS NOT NULL AND code_revision_source IS NOT NULL)",
            name="ck_evaluation_runs_code_revision_pair",
        ),
        CheckConstraint(
            "model IS NULL OR char_length(model) BETWEEN 1 AND 128",
            name="ck_evaluation_runs_model",
        ),
        CheckConstraint(
            "prompt_version IS NULL OR char_length(prompt_version) BETWEEN 1 AND 64",
            name="ck_evaluation_runs_prompt_version",
        ),
        CheckConstraint(
            "temperature IS NULL OR (temperature >= 0 AND temperature <= 2)",
            name="ck_evaluation_runs_temperature",
        ),
        CheckConstraint(
            "pricing_version IS NULL OR char_length(pricing_version) BETWEEN 1 AND 64",
            name="ck_evaluation_runs_pricing_version",
        ),
        CheckConstraint(
            "(suite = 'offline' AND model IS NULL AND prompt_version IS NULL "
            "AND temperature IS NULL AND pricing_version IS NULL) "
            "OR (suite = 'live' AND model IS NOT NULL AND prompt_version IS NOT NULL "
            "AND temperature IS NOT NULL AND pricing_version IS NOT NULL)",
            name="ck_evaluation_runs_live_fields",
        ),
        CheckConstraint(
            "provenance_level IN ('offline_recomputed', 'operator_imported')",
            name="ck_evaluation_runs_provenance",
        ),
        CheckConstraint(
            "(suite = 'offline' AND provenance_level = 'offline_recomputed') "
            "OR (suite = 'live' AND provenance_level = 'operator_imported')",
            name="ck_evaluation_runs_suite_provenance",
        ),
        CheckConstraint(
            "is_committed_baseline = false OR ("
            "suite = 'offline' AND outcome = 'passed' "
            "AND provenance_level = 'offline_recomputed')",
            name="ck_evaluation_runs_baseline",
        ),
        CheckConstraint(
            "jsonb_typeof(summary) = 'object'",
            name="ck_evaluation_runs_summary_object",
        ),
        CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name="ck_evaluation_runs_config_object",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    suite: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    artifact_sha256: Mapped[str] = mapped_column(Text, unique=True)
    dataset_version: Mapped[str] = mapped_column(Text)
    dataset_sha256: Mapped[str] = mapped_column(Text)
    report_version: Mapped[str] = mapped_column(Text)
    scorer_version: Mapped[str] = mapped_column(Text)
    k: Mapped[int] = mapped_column(Integer)
    code_revision: Mapped[str | None] = mapped_column(Text)
    code_revision_source: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    pricing_version: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provenance_level: Mapped[str] = mapped_column(Text)
    is_committed_baseline: Mapped[bool] = mapped_column(Boolean)
    summary: Mapped[dict[str, object]] = mapped_column(JSONB)
    config: Mapped[dict[str, object]] = mapped_column(JSONB)


class EvaluationCaseResult(Base):
    """One sanitized case. The composite key is the run, category, and case id."""

    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        CheckConstraint(
            "category IN ('retrieval', 'citation', 'grounding')",
            name="ck_evaluation_case_results_category",
        ),
        CheckConstraint(
            "case_id ~ '^[a-z0-9-]{1,64}$'",
            name="ck_evaluation_case_results_case_id",
        ),
        CheckConstraint(
            "jsonb_typeof(result) = 'object'",
            name="ck_evaluation_case_results_result_object",
        ),
    )

    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "evaluation_runs.id",
            name="fk_evaluation_case_results_evaluation_run_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(Text, primary_key=True)
    result: Mapped[dict[str, object]] = mapped_column(JSONB)
