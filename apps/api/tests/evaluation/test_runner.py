"""Offline CLI exit codes and stable JSON."""

import json
import shutil
from pathlib import Path

import pytest

from nexus.evaluation.__main__ import run_cli
from nexus.evaluation.datasets import DatasetError, default_fixture_dir, load_dataset
from nexus.evaluation.runner import offline_failed, run_offline


def test_offline_cli_exits_0_and_repeats_the_same_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "nested" / "second.json"

    assert run_cli(["--suite", "offline", "--output", str(first)]) == 0
    assert run_cli(["--suite", "offline", "--output", str(second)]) == 0

    text = first.read_text(encoding="utf-8")
    assert text == second.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert list(payload) == sorted(payload)
    case_ids = [case["case_id"] for case in payload["retrieval"]["cases"]]
    assert case_ids == sorted(case_ids)
    assert payload["dataset_version"] == "2026-10-19.2"
    assert payload["k"] == 5
    assert "Office plants need water" not in text
    assert payload["retrieval"]["aggregate"]["defined_cases"] == 15


def test_cli_exits_1_when_the_fixture_schema_fails(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "corpus.json").write_text("{", encoding="utf-8")
    output = tmp_path / "out.json"

    code = run_cli(
        [
            "--suite",
            "offline",
            "--output",
            str(output),
            "--fixtures",
            str(fixtures),
        ]
    )

    assert code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["error_code"] == "dataset"


def test_version_mismatch_is_a_dataset_error(tmp_path: Path) -> None:
    dest = tmp_path / "fixtures"
    shutil.copytree(default_fixture_dir(), dest)
    corpus_path = dest / "corpus.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    corpus["dataset_version"] = "1999-01-01.1"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

    with pytest.raises(DatasetError):
        load_dataset(dest)


def test_provenance_disagreement_exits_1(tmp_path: Path) -> None:
    report = run_offline(load_dataset())
    assert offline_failed(report) is False
    report.citations.cases[0].expect_citation_valid = not (
        report.citations.cases[0].citation_valid
    )
    assert offline_failed(report) is True


def test_live_cli_refuses_without_the_environment_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("NEXUS_RUN_LIVE_AI", raising=False)
    output = tmp_path / "out.json"

    code = run_cli(["--suite", "live", "--output", str(output)])

    assert code == 2
    assert not output.exists()
    assert "NEXUS_RUN_LIVE_AI" in capsys.readouterr().err


def test_live_cap_refuses_before_building_a_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NEXUS_RUN_LIVE_AI", "1")

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("client")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", boom)

    code = run_cli(
        [
            "--suite",
            "live",
            "--output",
            str(tmp_path / "out.json"),
            "--max-cases",
            "0",
        ]
    )

    assert code == 1
    assert not (tmp_path / "out.json").exists()


def test_production_modules_do_not_import_evaluation() -> None:
    root = Path("src/nexus")
    for path in root.rglob("*.py"):
        if "evaluation" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "nexus.evaluation" not in text
        assert "build_chat_model" not in Path(
            "src/nexus/evaluation/judge.py"
        ).read_text(encoding="utf-8")
