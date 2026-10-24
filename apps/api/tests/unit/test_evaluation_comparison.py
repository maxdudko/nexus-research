"""Comparison and cursors. These tests do not use a database."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from nexus.admin.comparison import RunView, compare_runs
from nexus.admin.evaluation_store import (
    decode_case_cursor,
    decode_evaluation_cursor,
    encode_case_cursor,
    encode_evaluation_cursor,
)
from nexus.admin.schemas import (
    CalibrationReport,
    CitationSummary,
    GroundingSummary,
    JudgeRunCounts,
    RetrievalSummary,
)
from nexus.ai.errors import ResearchError
from nexus.evaluation.calibration import LABELS

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")


def _retrieval(
    recall: str | None = "0.100000",
    mrr: str | None = None,
    defined: int = 2,
) -> RetrievalSummary:
    return RetrievalSummary(
        recall_at_k=recall,
        precision_at_k="0.250000",
        mrr_at_k=mrr,
        defined_cases=defined,
        undefined_cases=1,
    )


def _citations(valid: int = 1, invalid: int = 0) -> CitationSummary:
    return CitationSummary(cases=valid + invalid, valid=valid, invalid=invalid)


def _grounding() -> GroundingSummary:
    return GroundingSummary(cases=2, structurally_valid=2, structurally_invalid=0)


def _calibration(cell: int) -> CalibrationReport:
    matrix = {gold: {predicted: 0 for predicted in LABELS} for gold in LABELS}
    matrix["supported"]["supported"] = cell
    return CalibrationReport(
        labels=list(LABELS),
        matrix=matrix,
        compared=cell,
        agreements=cell,
        agreement="1.000000" if cell else None,
    )


def _view(**overrides: object) -> RunView:
    values: dict[str, object] = {
        "suite": "offline",
        "dataset_sha256": "ab" * 32,
        "dataset_version": "2026-10-19.2",
        "report_version": "1",
        "scorer_version": "1",
        "k": 5,
        "model": None,
        "prompt_version": None,
        "temperature": None,
        "label_taxonomy": None,
        "code_revision": "a" * 40,
        "executed_at": None,
        "pricing_version": None,
        "retrieval": _retrieval(),
        "citations": _citations(),
        "grounding": _grounding(),
        "counts": None,
        "calibration": None,
    }
    values.update(overrides)
    return RunView(**values)  # type: ignore[arg-type]


def _live(**overrides: object) -> RunView:
    values: dict[str, object] = {
        "suite": "live",
        "model": "gpt-4o-mini",
        "prompt_version": "grounding-judge-2",
        "temperature": Decimal("0.000"),
        "label_taxonomy": LABELS,
        "pricing_version": "2026-10-19",
        "counts": JudgeRunCounts(
            attempted=13,
            succeeded=13,
            failed=0,
            skipped=2,
            disagreed=0,
        ),
        "calibration": _calibration(11),
    }
    values.update(overrides)
    return _view(**values)


def test_compatible_offline_deltas_use_decimal_and_keep_nulls() -> None:
    result = compare_runs(
        _view(retrieval=_retrieval(mrr=None, defined=2), citations=_citations(1, 0)),
        _view(
            retrieval=_retrieval(recall="0.400000", mrr="0.500000", defined=4),
            citations=_citations(3, 1),
            code_revision="b" * 40,
        ),
    )
    assert result.comparable is True
    assert result.reasons == ()
    assert result.deltas is not None
    assert result.deltas.retrieval.recall_at_k == "0.300000"
    assert result.deltas.retrieval.mrr_at_k is None
    assert result.deltas.retrieval.defined_cases == 2
    assert result.deltas.citations.valid == 2
    assert result.deltas.citations.invalid == 1
    assert result.deltas.matrix is None
    assert result.deltas.counts is None
    assert result.deltas.agreement is None
    assert result.warnings == ("execution_time_unknown",)
    assert result.cost_comparable is False
    assert result.pricing_reason == "pricing_unavailable"


def test_incompatible_pairs_list_reasons_in_order_and_omit_deltas() -> None:
    mixed = compare_runs(
        _view(report_version="1", scorer_version="1", k=5),
        _view(
            suite="live",
            dataset_sha256="cd" * 32,
            dataset_version="other",
            report_version="2",
            scorer_version="9",
            k=10,
            model="gpt-4o-mini",
        ),
    )
    assert mixed.comparable is False
    assert mixed.deltas is None
    assert mixed.reasons == (
        "suite_mismatch",
        "dataset_mismatch",
        "report_version_mismatch",
        "scorer_mismatch",
        "k_mismatch",
    )
    live = compare_runs(
        _live(),
        _live(
            dataset_sha256="cd" * 32,
            report_version="2",
            scorer_version="9",
            k=8,
            model="other-model",
            prompt_version="other-prompt",
            temperature=Decimal("1.000"),
            label_taxonomy=("contradictory",) + LABELS[1:],
        ),
    )
    assert live.reasons == (
        "dataset_mismatch",
        "report_version_mismatch",
        "scorer_mismatch",
        "k_mismatch",
        "judge_config_mismatch",
    )
    assert live.deltas is None


def test_live_matrix_delta_and_pricing_flags() -> None:
    result = compare_runs(_live(), _live(calibration=_calibration(13)))
    assert result.comparable is True
    assert result.cost_comparable is True
    assert result.pricing_reason is None
    assert result.deltas is not None
    assert result.deltas.matrix is not None
    assert result.deltas.matrix["supported"]["supported"] == 2
    assert result.deltas.agreement == "0.000000"
    assert result.deltas.counts is not None
    assert result.deltas.counts.skipped == 0
    mismatch = compare_runs(_live(), _live(pricing_version="2026-11-01"))
    assert mismatch.cost_comparable is False
    assert mismatch.pricing_reason == "pricing_version_mismatch"
    missing = compare_runs(_live(), _live(pricing_version=None, code_revision=None))
    assert missing.pricing_reason == "pricing_unavailable"
    assert missing.warnings == ("execution_time_unknown", "code_revision_unknown")


def test_evaluation_cursors_round_trip_and_reject_tampering() -> None:
    encoded = encode_evaluation_cursor(NOW, RUN_ID)
    assert decode_evaluation_cursor(encoded) == (NOW, RUN_ID)
    case = encode_case_cursor("citation", "c01-claim")
    assert decode_case_cursor(case) == ("citation", "c01-claim")
    with pytest.raises(ResearchError) as caught:
        decode_evaluation_cursor(encoded + "tamper")
    assert caught.value.code == "invalid_request"
    assert caught.value.message == "cursor: invalid value"
    assert "tamper" not in caught.value.message
    with pytest.raises(ResearchError):
        decode_case_cursor(encode_case_cursor("notes", "c01-claim"))
