"""Blinded acceptance cannot pass missing, invalid, or regressed human reviews."""

import argparse
import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
HARNESS = runpy.run_path(str(ROOT / "scripts/research_benchmark.py"))


def test_benchmark_split_is_fixed():
    dataset = json.loads(
        (ROOT / "docs/evaluation/research-quality/questions.json").read_text()
    )
    cases = dataset["cases"]
    assert len({c["id"] for c in cases}) == 30
    assert sum(c["split"] == "development" for c in cases) == 20
    assert sum(c["split"] == "holdout" for c in cases) == 10
    assert all(c["question"] and c["criteria"] for c in cases)


@pytest.mark.parametrize("failure", [None, "wins", "regression", "missing", "invalid"])
def test_human_acceptance_gate(tmp_path, failure, capsys):
    rows = [
        {
            "case_id": f"rq{i}",
            "split": "holdout",
            "winner": "B" if i < 7 else "A",
            "rationale": "Reviewed original passages.",
            "A_supported_claims": 8,
            "A_total_claims": 10,
            "B_supported_claims": 8,
            "B_total_claims": 10,
        }
        for i in range(10)
    ]
    if failure == "wins":
        rows[0]["winner"] = "tie"
    elif failure == "regression":
        rows[0]["B_supported_claims"] = 7
    elif failure == "missing":
        rows = rows[:9]
    elif failure == "invalid":
        rows[0]["B_supported_claims"] = 11
    reviews, key = tmp_path / "reviews.json", tmp_path / "key.json"
    reviews.write_text(json.dumps(rows))
    key.write_text(json.dumps({f"rq{i}": "B" for i in range(10)}))
    args = argparse.Namespace(reviews=reviews, key=key)
    if failure:
        with pytest.raises(SystemExit):
            HARNESS["score"](args)
    else:
        HARNESS["score"](args)
    output = json.loads(capsys.readouterr().out)
    assert output["accepted"] is (failure is None)


def test_capture_never_starts_paid_calls_without_explicit_opt_in():
    with pytest.raises(SystemExit, match="allow-paid"):
        HARNESS["capture"](argparse.Namespace(allow_paid=False))


def test_capture_uses_stream_and_reads_the_committed_report():
    import httpx

    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/stream"):
            assert json.loads(request.content)["depth"] == "deep"
            return httpx.Response(
                200,
                text=(
                    'event: research.started\ndata: {"run_id":"run"}\n\n'
                    ': ping\n\nevent: research.completed\ndata: {"run_id":"run"}\n\n'
                ),
            )
        return httpx.Response(
            200,
            json={
                "run": {
                    "research_id": "scope",
                    "status": "partial",
                    "warnings": [],
                    "usage": {},
                    "depth": "deep",
                    "applied_limits": {"deadline_seconds": 600},
                },
                "report": {"title": "Report"},
            },
        )

    with httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(handle)
    ) as client:
        result, error, run_id = HARNESS["stream_capture"](
            client, {"question": "test", "depth": "deep"}
        )
    assert error is None and run_id == "run"
    assert result["report"] == {"title": "Report"}
    assert result["applied_limits"]["deadline_seconds"] == 600
    assert paths == ["/api/v1/research/stream", "/api/v1/research/runs/run"]
