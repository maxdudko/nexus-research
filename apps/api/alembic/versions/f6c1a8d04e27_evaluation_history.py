"""evaluation history

Revision ID: f6c1a8d04e27
Revises: e3b7d91c4a20
Create Date: 2026-10-24 12:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6c1a8d04e27"
down_revision: str | Sequence[str] | None = "e3b7d91c4a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add evaluation history. Research tables are unchanged."""
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("suite", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("artifact_sha256", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("dataset_sha256", sa.Text(), nullable=False),
        sa.Column("report_version", sa.Text(), nullable=False),
        sa.Column("scorer_version", sa.Text(), nullable=False),
        sa.Column("k", sa.Integer(), nullable=False),
        sa.Column("code_revision", sa.Text(), nullable=True),
        sa.Column("code_revision_source", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("temperature", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("pricing_version", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance_level", sa.Text(), nullable=False),
        sa.Column("is_committed_baseline", sa.Boolean(), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "suite IN ('offline', 'live')",
            name="ck_evaluation_runs_suite",
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'informational')",
            name="ck_evaluation_runs_outcome",
        ),
        sa.CheckConstraint(
            "(suite = 'offline' AND outcome IN ('passed', 'failed')) "
            "OR (suite = 'live' AND outcome = 'informational')",
            name="ck_evaluation_runs_suite_outcome",
        ),
        sa.CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_runs_artifact_sha256",
        ),
        sa.CheckConstraint(
            "dataset_version ~ '^[A-Za-z0-9._-]{1,64}$'",
            name="ck_evaluation_runs_dataset_version",
        ),
        sa.CheckConstraint(
            "dataset_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_runs_dataset_sha256",
        ),
        sa.CheckConstraint(
            "report_version = '1'",
            name="ck_evaluation_runs_report_version",
        ),
        sa.CheckConstraint(
            "scorer_version = '1'",
            name="ck_evaluation_runs_scorer_version",
        ),
        sa.CheckConstraint(
            "k >= 1 AND k <= 100",
            name="ck_evaluation_runs_k",
        ),
        sa.CheckConstraint(
            "code_revision IS NULL OR code_revision ~ '^[0-9a-f]{40}$'",
            name="ck_evaluation_runs_code_revision",
        ),
        sa.CheckConstraint(
            "code_revision_source IS NULL "
            "OR code_revision_source = 'operator_asserted'",
            name="ck_evaluation_runs_code_revision_source",
        ),
        sa.CheckConstraint(
            "(code_revision IS NULL AND code_revision_source IS NULL) "
            "OR (code_revision IS NOT NULL AND code_revision_source IS NOT NULL)",
            name="ck_evaluation_runs_code_revision_pair",
        ),
        sa.CheckConstraint(
            "model IS NULL OR char_length(model) BETWEEN 1 AND 128",
            name="ck_evaluation_runs_model",
        ),
        sa.CheckConstraint(
            "prompt_version IS NULL OR char_length(prompt_version) BETWEEN 1 AND 64",
            name="ck_evaluation_runs_prompt_version",
        ),
        sa.CheckConstraint(
            "temperature IS NULL OR (temperature >= 0 AND temperature <= 2)",
            name="ck_evaluation_runs_temperature",
        ),
        sa.CheckConstraint(
            "pricing_version IS NULL OR char_length(pricing_version) BETWEEN 1 AND 64",
            name="ck_evaluation_runs_pricing_version",
        ),
        sa.CheckConstraint(
            "(suite = 'offline' AND model IS NULL AND prompt_version IS NULL "
            "AND temperature IS NULL AND pricing_version IS NULL) "
            "OR (suite = 'live' AND model IS NOT NULL AND prompt_version IS NOT NULL "
            "AND temperature IS NOT NULL AND pricing_version IS NOT NULL)",
            name="ck_evaluation_runs_live_fields",
        ),
        sa.CheckConstraint(
            "provenance_level IN ('offline_recomputed', 'operator_imported')",
            name="ck_evaluation_runs_provenance",
        ),
        sa.CheckConstraint(
            "(suite = 'offline' AND provenance_level = 'offline_recomputed') "
            "OR (suite = 'live' AND provenance_level = 'operator_imported')",
            name="ck_evaluation_runs_suite_provenance",
        ),
        sa.CheckConstraint(
            "is_committed_baseline = false OR ("
            "suite = 'offline' AND outcome = 'passed' "
            "AND provenance_level = 'offline_recomputed')",
            name="ck_evaluation_runs_baseline",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(summary) = 'object'",
            name="ck_evaluation_runs_summary_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name="ck_evaluation_runs_config_object",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_sha256"),
    )
    op.create_index(
        "ix_evaluation_runs_imported_at_id",
        "evaluation_runs",
        [sa.text("imported_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_table(
        "evaluation_case_results",
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "category IN ('retrieval', 'citation', 'grounding')",
            name="ck_evaluation_case_results_category",
        ),
        sa.CheckConstraint(
            "case_id ~ '^[a-z0-9-]{1,64}$'",
            name="ck_evaluation_case_results_case_id",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result) = 'object'",
            name="ck_evaluation_case_results_result_object",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name="fk_evaluation_case_results_evaluation_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("evaluation_run_id", "category", "case_id"),
    )


def downgrade() -> None:
    """Drop evaluation history. Research rows stay in place."""
    op.drop_table("evaluation_case_results")
    op.drop_index(
        "ix_evaluation_runs_imported_at_id",
        table_name="evaluation_runs",
    )
    op.drop_table("evaluation_runs")
