"""Import validation without a database."""

import hashlib
import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from tests.conftest import make_settings

from nexus.admin.errors import (
    ARTIFACT_INVALID,
    BAD_REVISION,
    FILE_MISSING,
    LIVE_MISMATCH,
    OFFLINE_MISMATCH,
    STORE_UNAVAILABLE,
    TOO_LARGE,
    VERSION_UNSUPPORTED,
)
from nexus.admin.evaluation_import import committed_baseline_path, run_cli
from nexus.admin.normalization import prepare_import
from nexus.admin.provenance import dataset_sha256, normalize_code_revision
from nexus.admin.schemas import GroundingJudgeResultPayload
from nexus.evaluation.datasets import default_fixture_dir, load_dataset
from nexus.evaluation.runner import dump_report, run_offline
from nexus.evaluation.schemas import SCORER_VERSION, OfflineReport

ROOT = Path(__file__).resolve().parents[4]
BASELINE = ROOT / "docs" / "evaluation" / "baseline.json"
LIVE = ROOT / "docs" / "evaluation" / "outputs" / "nexus-eval-live.json"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
DATASET_SHA = "be4bf734127c3b152430855b133f9e6751f7a9955ed8f52b8471cc4a0a0fce68"
BASELINE_SHA = "1b872057d429dd664de33fc7efc76bb18be1f82f2672e50f273fe14ac76fabc7"


def test_fixture_manifest_matches_the_shipped_files() -> None:
    assert dataset_sha256(default_fixture_dir()) == DATASET_SHA


def test_report_schema_does_not_carry_scorer_version() -> None:
    assert SCORER_VERSION == "1"
    assert "scorer_version" not in OfflineReport.model_fields


def test_offline_recompute_matches_the_committed_baseline() -> None:
    text = dump_report(run_offline(load_dataset()))
    assert text == BASELINE.read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == BASELINE_SHA


def test_committed_baseline_path_is_the_repository_file() -> None:
    path = committed_baseline_path()
    assert path == BASELINE
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == BASELINE_SHA


def test_live_prepare_strips_text_and_does_not_call_the_judge(
    monkeypatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("run_live")

    monkeypatch.setattr("nexus.evaluation.runner.run_live", explode)
    raw = LIVE.read_bytes()
    payload = prepare_import(
        raw,
        fixtures=None,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        code_revision=None,
        code_revision_source=None,
        committed_baseline=False,
        baseline_bytes=None,
        imported_at=NOW,
        run_id=uuid4(),
    )
    dumped = json.dumps(payload.model_dump(mode="json"))
    _assert_absent(dumped, json.loads(raw.decode("utf-8")))
    assert payload.suite == "live"
    assert payload.provenance_level == "operator_imported"
    assert payload.executed_at is None
    assert payload.summary.calibration.agreements == 11
    assert payload.summary.calibration.compared == 13


def test_missing_oversize_version_and_revision_use_fixed_messages(
    tmp_path: Path,
) -> None:
    missing = StringIO()
    assert run_cli(["--artifact", str(tmp_path / "missing.json")], stderr=missing) == 1
    assert missing.getvalue().strip() == FILE_MISSING

    huge = tmp_path / "huge.json"
    huge.write_bytes(b"{" * (2_097_152 + 1))
    oversize = StringIO()
    assert run_cli(["--artifact", str(huge)], stderr=oversize) == 1
    assert oversize.getvalue().strip() == TOO_LARGE

    truncated = tmp_path / "truncated.json"
    truncated.write_text("{", encoding="utf-8")
    invalid = StringIO()
    assert run_cli(["--artifact", str(truncated)], stderr=invalid) == 1
    assert invalid.getvalue().strip() == ARTIFACT_INVALID

    version = tmp_path / "version.json"
    version.write_text('{"report_version":"2","suite":"offline"}\n', encoding="utf-8")
    unsupported = StringIO()
    assert run_cli(["--artifact", str(version)], stderr=unsupported) == 1
    assert unsupported.getvalue().strip() == VERSION_UNSUPPORTED

    bad_sha = StringIO()
    code = run_cli(
        ["--artifact", str(BASELINE), "--code-revision", "abc"],
        stderr=bad_sha,
    )
    assert code == 1
    assert bad_sha.getvalue().strip() == BAD_REVISION
    assert normalize_code_revision("A" * 40) == "a" * 40


def test_changed_offline_artifact_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    payload["retrieval"]["aggregate"]["recall_at_k"] = "0.000000"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")
    stderr = StringIO()
    assert run_cli(["--artifact", str(changed)], stderr=stderr) == 1
    assert stderr.getvalue().strip() == OFFLINE_MISMATCH


def _judge(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "structural_valid": True,
        "expect_structurally_valid": True,
        "gold_label": "supported",
        "error_code": None,
        "excerpt_count": 1,
        "predicted_label": "supported",
        "outcome": "ok",
        "model": "gpt-4o-mini",
        "prompt_version": "grounding-judge-2",
    }
    values.update(overrides)
    return values


def test_live_judge_rows_accept_the_three_consistent_shapes() -> None:
    GroundingJudgeResultPayload.model_validate(_judge())
    GroundingJudgeResultPayload.model_validate(
        _judge(
            structural_valid=False,
            predicted_label=None,
            outcome="skipped",
            model=None,
            prompt_version=None,
            error_code="foreign_research",
        )
    )
    GroundingJudgeResultPayload.model_validate(
        _judge(
            predicted_label=None,
            outcome="error",
            model=None,
            prompt_version=None,
            error_code="invalid_model_output",
        )
    )
    GroundingJudgeResultPayload.model_validate(
        _judge(
            predicted_label=None,
            outcome="error",
            error_code="unknown_citation",
        )
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"predicted_label": None},
        {"error_code": "provider_unavailable"},
        {"model": None},
        {"prompt_version": None},
        {"structural_valid": False},
        {
            "structural_valid": True,
            "outcome": "skipped",
            "predicted_label": None,
            "model": None,
            "prompt_version": None,
            "error_code": "foreign_research",
        },
        {
            "structural_valid": False,
            "outcome": "skipped",
            "predicted_label": "supported",
            "model": None,
            "prompt_version": None,
            "error_code": "foreign_research",
        },
        {
            "structural_valid": False,
            "outcome": "skipped",
            "predicted_label": None,
            "model": "gpt-4o-mini",
            "prompt_version": None,
            "error_code": "foreign_research",
        },
        {
            "structural_valid": False,
            "outcome": "skipped",
            "predicted_label": None,
            "model": None,
            "prompt_version": None,
            "error_code": None,
        },
        {
            "predicted_label": "supported",
            "outcome": "error",
            "error_code": "unknown_citation",
        },
        {
            "predicted_label": None,
            "outcome": "error",
            "model": "gpt-4o-mini",
            "prompt_version": None,
            "error_code": "unknown_citation",
        },
        {
            "structural_valid": False,
            "predicted_label": None,
            "outcome": "error",
            "model": None,
            "prompt_version": None,
            "error_code": "unknown_citation",
        },
    ],
)
def test_contradictory_live_judge_metadata_is_rejected(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        GroundingJudgeResultPayload.model_validate(_judge(**overrides))


def test_forged_live_calibration_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(LIVE.read_text(encoding="utf-8"))
    payload["calibration"]["agreements"] = 0
    payload["calibration"]["agreement"] = "0.000000"
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    stderr = StringIO()
    assert run_cli(["--artifact", str(forged)], stderr=stderr) == 1
    assert stderr.getvalue().strip() == LIVE_MISMATCH


def test_database_failure_does_not_echo_the_url(monkeypatch) -> None:
    def fail(_factory: object, _payload: object) -> None:
        raise RuntimeError("postgresql://super-secret-db")

    monkeypatch.setattr("nexus.admin.evaluation_import.insert_evaluation", fail)
    stderr = StringIO()
    code = run_cli(
        ["--artifact", str(BASELINE), "--committed-baseline"],
        stderr=stderr,
        settings=make_settings(),
    )
    assert code == 1
    assert stderr.getvalue().strip() == STORE_UNAVAILABLE
    assert "super-secret-db" not in stderr.getvalue()


def _assert_absent(dumped: str, artifact: dict[str, object]) -> None:
    for row in artifact["grounding"]:
        if not isinstance(row, dict):
            continue
        rationale = row.get("rationale")
        if isinstance(rationale, str):
            assert rationale not in dumped
        for key in ("rationale", "input_tokens", "output_tokens", "timestamp"):
            assert f'"{key}"' not in dumped
    fixtures = default_fixture_dir()
    for name, field in (
        ("queries.json", "question"),
        ("citation_cases.json", "claim"),
        ("grounding_cases.json", "claim"),
    ):
        document = json.loads((fixtures / name).read_text(encoding="utf-8"))
        for case in document["cases"]:
            assert case[field] not in dumped
    corpus = json.loads((fixtures / "corpus.json").read_text(encoding="utf-8"))
    for chunk in corpus["chunks"]:
        assert chunk["text"] not in dumped
        if chunk["url"] is not None:
            assert chunk["url"] not in dumped
