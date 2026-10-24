"""Turn a validated report into sanitized rows."""

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from nexus.admin.errors import (
    ARTIFACT_INVALID,
    BASELINE_MISMATCH,
    BASELINE_OFFLINE,
    FIXTURES_INVALID,
    LIVE_MISMATCH,
    OFFLINE_MISMATCH,
    VERSION_UNSUPPORTED,
    EvaluationImportError,
)
from nexus.admin.provenance import dataset_sha256
from nexus.admin.schemas import (
    CitationResult,
    CitationSummary,
    EvaluationRunInsert,
    GroundingJudgeResultPayload,
    GroundingStructureResultPayload,
    GroundingSummary,
    LiveConfig,
    LiveSummary,
    OfflineConfig,
    OfflineSummary,
    RetrievalResult,
    RetrievalSummary,
    StoredCase,
)
from nexus.evaluation.calibration import LABELS, calibrate, judge_counts
from nexus.evaluation.citations import check_citations
from nexus.evaluation.datasets import (
    DatasetError,
    EvaluationDataset,
    default_fixture_dir,
    load_dataset,
)
from nexus.evaluation.grounding import grounding_structure
from nexus.evaluation.retrieval import score_dataset
from nexus.evaluation.runner import offline_failed, run_offline
from nexus.evaluation.schemas import (
    SCORER_VERSION,
    CitationReport,
    GroundingJudgeResult,
    GroundingStructureResult,
    LiveReport,
    OfflineReport,
    RetrievalReport,
)

RevisionSource = Literal["operator_asserted"]


def prepare_import(
    raw: bytes,
    *,
    fixtures: Path | None,
    artifact_sha256: str,
    code_revision: str | None,
    code_revision_source: RevisionSource | None,
    committed_baseline: bool,
    baseline_bytes: bytes | None,
    imported_at: datetime,
    run_id: UUID,
) -> EvaluationRunInsert:
    """Validate one artifact and return the row that may be inserted."""
    if committed_baseline and baseline_bytes != raw:
        raise EvaluationImportError(BASELINE_MISMATCH)
    report = parse_report(raw)
    if committed_baseline and not isinstance(report, OfflineReport):
        raise EvaluationImportError(BASELINE_OFFLINE)
    dataset, digest = _dataset(fixtures)
    if isinstance(report, OfflineReport):
        return _offline(
            report,
            dataset,
            digest,
            artifact_sha256=artifact_sha256,
            code_revision=code_revision,
            code_revision_source=code_revision_source,
            committed_baseline=committed_baseline,
            imported_at=imported_at,
            run_id=run_id,
        )
    return _live(
        report,
        dataset,
        digest,
        artifact_sha256=artifact_sha256,
        code_revision=code_revision,
        code_revision_source=code_revision_source,
        imported_at=imported_at,
        run_id=run_id,
    )


def parse_report(raw: bytes) -> OfflineReport | LiveReport:
    """Read report version 1. Unknown versions are rejected before field checks."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeError, json.JSONDecodeError:
        raise EvaluationImportError(ARTIFACT_INVALID) from None
    if not isinstance(payload, dict):
        raise EvaluationImportError(ARTIFACT_INVALID)
    if payload.get("report_version") != "1":
        if "report_version" in payload:
            raise EvaluationImportError(VERSION_UNSUPPORTED)
        raise EvaluationImportError(ARTIFACT_INVALID)
    suite = payload.get("suite")
    model: type[OfflineReport | LiveReport]
    if suite == "offline":
        model = OfflineReport
    elif suite == "live":
        model = LiveReport
    else:
        raise EvaluationImportError(ARTIFACT_INVALID)
    try:
        return model.model_validate(payload)
    except ValidationError:
        raise EvaluationImportError(ARTIFACT_INVALID) from None


def _dataset(fixtures: Path | None) -> tuple[EvaluationDataset, str]:
    root = default_fixture_dir() if fixtures is None else fixtures
    try:
        dataset = load_dataset(root)
        return dataset, dataset_sha256(root)
    except DatasetError, OSError:
        raise EvaluationImportError(FIXTURES_INVALID) from None


def _offline(
    report: OfflineReport,
    dataset: EvaluationDataset,
    dataset_digest: str,
    *,
    artifact_sha256: str,
    code_revision: str | None,
    code_revision_source: RevisionSource | None,
    committed_baseline: bool,
    imported_at: datetime,
    run_id: UUID,
) -> EvaluationRunInsert:
    recomputed = run_offline(dataset)
    if report.model_dump(mode="json") != recomputed.model_dump(mode="json"):
        raise EvaluationImportError(OFFLINE_MISMATCH)
    outcome: Literal["passed", "failed"] = (
        "failed" if offline_failed(recomputed) else "passed"
    )
    if committed_baseline and outcome != "passed":
        raise EvaluationImportError(BASELINE_MISMATCH)
    try:
        return EvaluationRunInsert(
            id=run_id,
            suite="offline",
            outcome=outcome,
            artifact_sha256=artifact_sha256,
            dataset_version=dataset.version,
            dataset_sha256=dataset_digest,
            report_version="1",
            scorer_version=SCORER_VERSION,
            k=recomputed.k,
            code_revision=code_revision,
            code_revision_source=code_revision_source,
            executed_at=None,
            imported_at=imported_at,
            provenance_level="offline_recomputed",
            is_committed_baseline=committed_baseline,
            summary=_summary(
                recomputed.retrieval,
                recomputed.citations,
                [case.structural_valid for case in recomputed.grounding],
            ),
            config=OfflineConfig(empty_gold="undefined", metric_quant="0.000001"),
            cases=_offline_cases(recomputed),
        )
    except ValidationError:
        raise EvaluationImportError(ARTIFACT_INVALID) from None


def _live(
    report: LiveReport,
    dataset: EvaluationDataset,
    dataset_digest: str,
    *,
    artifact_sha256: str,
    code_revision: str | None,
    code_revision_source: RevisionSource | None,
    imported_at: datetime,
    run_id: UUID,
) -> EvaluationRunInsert:
    _require_live(report, dataset)
    structures = {item.case_id: item for item in grounding_structure(dataset)}
    summary = _summary(
        report.retrieval,
        report.citations,
        [case.structural_valid for case in report.grounding],
    )
    try:
        return EvaluationRunInsert(
            id=run_id,
            suite="live",
            outcome="informational",
            artifact_sha256=artifact_sha256,
            dataset_version=dataset.version,
            dataset_sha256=dataset_digest,
            report_version="1",
            scorer_version=SCORER_VERSION,
            k=report.k,
            code_revision=code_revision,
            code_revision_source=code_revision_source,
            model=report.model,
            prompt_version=report.prompt_version,
            temperature=Decimal(str(report.temperature)),
            pricing_version=report.pricing_version,
            executed_at=None,
            imported_at=imported_at,
            provenance_level="operator_imported",
            is_committed_baseline=False,
            summary=LiveSummary(
                retrieval=summary.retrieval,
                citations=summary.citations,
                grounding=summary.grounding,
                counts=report.counts,
                calibration=report.calibration,
            ),
            config=LiveConfig(
                empty_gold="undefined",
                metric_quant="0.000001",
                label_taxonomy=list(LABELS),
            ),
            cases=[
                *_metric_cases(report.retrieval, report.citations),
                *_judge_cases(report.grounding, structures),
            ],
        )
    except ValidationError:
        raise EvaluationImportError(LIVE_MISMATCH) from None


def _require_live(report: LiveReport, dataset: EvaluationDataset) -> None:
    if report.dataset_version != dataset.version:
        raise EvaluationImportError(LIVE_MISMATCH)
    if report.retrieval != score_dataset(dataset):
        raise EvaluationImportError(LIVE_MISMATCH)
    if report.citations != check_citations(dataset):
        raise EvaluationImportError(LIVE_MISMATCH)
    structures = {item.case_id: item for item in grounding_structure(dataset)}
    fixtures = {case.case_id: case for case in dataset.grounding}
    seen: set[str] = set()
    for item in report.grounding:
        fixture = fixtures.get(item.case_id)
        structure = structures.get(item.case_id)
        if (
            item.case_id in seen
            or fixture is None
            or structure is None
            or item.gold_label != fixture.gold_label
            or item.structural_valid != structure.structural_valid
            or item.dataset_version != dataset.version
        ):
            raise EvaluationImportError(LIVE_MISMATCH)
        seen.add(item.case_id)
    if seen != set(fixtures):
        raise EvaluationImportError(LIVE_MISMATCH)
    if judge_counts(report.grounding) != report.counts:
        raise EvaluationImportError(LIVE_MISMATCH)
    if calibrate(report.grounding) != report.calibration:
        raise EvaluationImportError(LIVE_MISMATCH)


def _summary(
    retrieval: RetrievalReport,
    citations: CitationReport,
    structural_flags: Sequence[bool],
) -> OfflineSummary:
    valid = sum(1 for case in citations.cases if case.citation_valid)
    structural = sum(1 for flag in structural_flags if flag)
    aggregate = retrieval.aggregate
    return OfflineSummary(
        retrieval=RetrievalSummary(
            recall_at_k=aggregate.recall_at_k,
            precision_at_k=aggregate.precision_at_k,
            mrr_at_k=aggregate.mrr_at_k,
            defined_cases=aggregate.defined_cases,
            undefined_cases=aggregate.undefined_cases,
        ),
        citations=CitationSummary(
            cases=len(citations.cases),
            valid=valid,
            invalid=len(citations.cases) - valid,
        ),
        grounding=GroundingSummary(
            cases=len(structural_flags),
            structurally_valid=structural,
            structurally_invalid=len(structural_flags) - structural,
        ),
    )


def _offline_cases(report: OfflineReport) -> list[StoredCase]:
    return [
        *_metric_cases(report.retrieval, report.citations),
        *[
            StoredCase(
                category="grounding",
                case_id=case.case_id,
                result=_structure_result(case),
            )
            for case in report.grounding
        ],
    ]


def _metric_cases(
    retrieval: RetrievalReport,
    citations: CitationReport,
) -> list[StoredCase]:
    cases = [
        StoredCase(
            category="retrieval",
            case_id=case.case_id,
            result=RetrievalResult(
                gold_count=case.gold_count,
                recall_at_k=case.recall_at_k,
                precision_at_k=case.precision_at_k,
                mrr_at_k=case.mrr_at_k,
            ),
        )
        for case in retrieval.cases
    ]
    cases.extend(
        StoredCase(
            category="citation",
            case_id=case.case_id,
            result=CitationResult(
                citation_valid=case.citation_valid,
                expect_citation_valid=case.expect_citation_valid,
                support_label=case.support_label,
                error_codes=list(case.error_codes),
            ),
        )
        for case in citations.cases
    )
    return cases


def _structure_result(
    case: GroundingStructureResult,
) -> GroundingStructureResultPayload:
    return GroundingStructureResultPayload(
        structural_valid=case.structural_valid,
        expect_structurally_valid=case.expect_structurally_valid,
        gold_label=case.gold_label,
        error_code=case.error_code,
        excerpt_count=case.excerpt_count,
    )


def _judge_cases(
    grounding: Sequence[GroundingJudgeResult],
    structures: dict[str, GroundingStructureResult],
) -> list[StoredCase]:
    cases: list[StoredCase] = []
    for item in grounding:
        structure = structures[item.case_id]
        cases.append(
            StoredCase(
                category="grounding",
                case_id=item.case_id,
                result=GroundingJudgeResultPayload(
                    structural_valid=item.structural_valid,
                    expect_structurally_valid=structure.expect_structurally_valid,
                    gold_label=item.gold_label,
                    error_code=item.error_code,
                    excerpt_count=structure.excerpt_count,
                    predicted_label=item.predicted_label,
                    outcome=item.outcome,
                    model=item.model,
                    prompt_version=item.prompt_version,
                ),
            )
        )
    return cases
