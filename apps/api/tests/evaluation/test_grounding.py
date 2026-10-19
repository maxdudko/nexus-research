"""Grounding validation, a fake judge, and calibration."""

import io
import logging
from collections.abc import Iterator
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.conftest import make_settings

from nexus.evaluation.calibration import calibrate, judge_counts
from nexus.evaluation.datasets import FIXTURE_CLAIM_TOKEN, Catalog, load_dataset
from nexus.evaluation.grounding import (
    evaluate_grounding,
    grounding_structure,
    prepare_claim,
)
from nexus.evaluation.judge import OpenAIGroundingJudge, ScriptedJudge
from nexus.evaluation.schemas import (
    DATASET_VERSION,
    EXCERPT_CHARS,
    CorpusChunk,
    GroundingCase,
    GroundingJudgeResult,
    JudgeVerdict,
    PreparedClaim,
    PreparedExcerpt,
)
from nexus.observability.logging import configure_logging
from nexus.schemas.report import ResearchReport


@pytest.fixture(autouse=True)
def restore_nexus_logger() -> Iterator[None]:
    logger = logging.getLogger("nexus")
    handlers = list(logger.handlers)
    propagate = logger.propagate
    level = logger.level
    yield
    logger.handlers = handlers
    logger.propagate = propagate
    logger.setLevel(level)


def test_validator_cuts_excerpts_and_drops_uncited_text() -> None:
    long_text = ("a" * EXCERPT_CHARS) + "TAIL"
    cited = _chunk("web-limits#c0", long_text)
    uncited = _chunk("web-noise#c0", "Office plants need water twice a week.")
    catalog = Catalog(
        claim_token=FIXTURE_CLAIM_TOKEN,
        chunks={cited.semantic_id: cited, uncited.semantic_id: uncited},
    )
    case = _case("g-cut", [cited.semantic_id], "supported", True)

    prepared = prepare_claim(case, catalog)

    assert isinstance(prepared, PreparedClaim)
    assert len(prepared.excerpts) == 1
    assert len(prepared.excerpts[0].text) == EXCERPT_CHARS
    assert "TAIL" not in prepared.excerpts[0].text
    assert "Office plants" not in prepared.excerpts[0].text


def test_unknown_citation_and_foreign_scope_are_rejected() -> None:
    dataset = load_dataset()
    structure = {item.case_id: item for item in grounding_structure(dataset)}

    assert structure["g10-unknown"].structural_valid is False
    assert structure["g10-unknown"].error_code == "unknown_citation"
    assert structure["g09-foreign"].structural_valid is False
    assert structure["g09-foreign"].error_code == "foreign_research"
    assert structure["g02-spurious"].structural_valid is True
    assert structure["g02-spurious"].gold_label == "insufficient_context"
    assert structure["g08-wrong-number"].gold_label == "unsupported"
    assert structure["g13-explicit-deny"].gold_label == "unsupported"
    assert structure["g14-missing-day"].gold_label == "insufficient_context"
    assert structure["g15-off-topic-conflict"].gold_label == "insufficient_context"
    assert structure["g15-off-topic-conflict"].excerpt_count == 2
    assert structure["g04-contradictory"].excerpt_count == 2


def test_judge_verdict_rejects_an_unknown_label() -> None:
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate(
            {"verdict": "maybe", "rationale": "no", "evidence_ids": []}
        )


async def test_fake_judge_records_contradictory_evidence_without_a_report() -> None:
    dataset = load_dataset()
    report = _report()
    before = report.model_dump()
    judge = _scripted(dataset)

    results = await evaluate_grounding(dataset, judge)

    assert report.model_dump() == before
    found = {item.case_id: item for item in results}
    assert "g09-foreign" not in judge.seen
    assert "g10-unknown" not in judge.seen
    assert found["g04-contradictory"].predicted_label == "contradictory"
    assert found["g05-uncertain"].predicted_label == "insufficient_context"
    assert found["g02-spurious"].predicted_label == "insufficient_context"
    assert found["g10-unknown"].outcome == "skipped"


async def test_unknown_evidence_id_from_the_judge_is_not_a_label() -> None:
    dataset = load_dataset()
    verdicts = _verdicts(dataset)
    verdicts["g01-supported"] = JudgeVerdict(
        verdict="supported",
        rationale="cites a passage that was not provided",
        evidence_ids=["missing#c9"],
    )

    results = await evaluate_grounding(dataset, ScriptedJudge(verdicts))
    found = next(item for item in results if item.case_id == "g01-supported")

    assert found.outcome == "error"
    assert found.error_code == "unknown_citation"
    assert found.predicted_label is None
    assert found.rationale is None


async def test_timeout_is_recorded_and_does_not_require_a_report() -> None:
    dataset = load_dataset()
    verdicts: dict[str, JudgeVerdict | Exception] = dict(_verdicts(dataset))
    verdicts["g01-supported"] = TimeoutError("slow UNIQUE_TIMEOUT")
    report = _report()
    before = report.model_dump()

    results = await evaluate_grounding(dataset, ScriptedJudge(verdicts))

    found = next(item for item in results if item.case_id == "g01-supported")
    assert report.model_dump() == before
    assert found.outcome == "error"
    assert found.error_code == "timeout"
    assert found.input_tokens is None
    assert found.output_tokens is None
    assert "UNIQUE_TIMEOUT" not in found.model_dump_json()


async def test_a_non_verdict_is_invalid_model_output() -> None:
    dataset = load_dataset()

    class BadJudge:
        async def judge(self, prepared: PreparedClaim) -> object:
            return {"verdict": "maybe"}

    results = await evaluate_grounding(dataset, BadJudge())  # type: ignore[arg-type]
    found = next(item for item in results if item.case_id == "g01-supported")

    assert found.outcome == "error"
    assert found.error_code == "invalid_model_output"


async def test_judge_logs_omit_claim_excerpt_and_rationale() -> None:
    stream = io.StringIO()
    configure_logging(make_settings(log_format="json"), stream=stream)
    judge = OpenAIGroundingJudge(make_settings(openai_api_key="sk-test-secret"))
    prepared = PreparedClaim(
        case_id="g01-supported",
        claim="UNIQUE_CLAIM about planning iterations",
        excerpts=[
            PreparedExcerpt(semantic_id="web-limits#c0", text="UNIQUE_EXCERPT passage")
        ],
    )
    verdict = JudgeVerdict(
        verdict="supported",
        rationale="UNIQUE_RATIONALE not for logs",
        evidence_ids=["web-limits#c0"],
    )

    class Runnable:
        async def ainvoke(self, messages: object) -> dict[str, object]:
            return {"parsed": verdict, "raw": None, "parsing_error": None}

    judge._runnable = lambda: Runnable()  # type: ignore[method-assign]
    result = await judge.judge(prepared)
    text = stream.getvalue()

    assert result.outcome == "ok"
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.usage_source == "unavailable"
    assert "grounding_judge" in text
    assert "UNIQUE_CLAIM" not in text
    assert "UNIQUE_EXCERPT" not in text
    assert "UNIQUE_RATIONALE" not in text
    assert "sk-test-secret" not in text


def test_constructing_the_judge_does_not_build_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("client")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", boom)
    OpenAIGroundingJudge(make_settings())


async def test_live_counts_keep_disagreement_out_of_failures() -> None:
    dataset = load_dataset()
    verdicts: dict[str, JudgeVerdict | Exception] = dict(_verdicts(dataset))
    verdicts["g01-supported"] = TimeoutError("slow")
    verdicts["g08-wrong-number"] = JudgeVerdict(
        verdict="supported",
        rationale="disagrees with gold",
        evidence_ids=["web-limits#c0"],
    )

    results = await evaluate_grounding(dataset, ScriptedJudge(verdicts))
    counts = judge_counts(results)
    calibration = calibrate(results)

    assert counts.attempted == 13
    assert counts.succeeded == 12
    assert counts.failed == 1
    assert counts.skipped == 2
    assert counts.disagreed == 1
    assert calibration.compared == 12
    assert calibration.agreements == 11


def test_calibration_counts_agreement_as_an_estimate() -> None:
    results = [
        _result("supported", "supported", "ok"),
        _result("supported", "unsupported", "ok"),
        _result("contradictory", None, "error"),
    ]

    report = calibrate(results)

    assert report.estimate is True
    assert report.compared == 2
    assert report.agreements == 1
    assert report.agreement == "0.500000"
    assert report.matrix["supported"]["unsupported"] == 1
    assert report.matrix["contradictory"]["contradictory"] == 0


def _chunk(semantic_id: str, text: str) -> CorpusChunk:
    return CorpusChunk(
        semantic_id=semantic_id,
        research_id=UUID("11111111-1111-4111-8111-111111111111"),
        source_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
        chunk_index=0,
        kind="web",
        url="https://example.test/limits",
        text=text,
    )


def _case(
    case_id: str,
    cited: list[str],
    label: str,
    valid: bool,
) -> GroundingCase:
    return GroundingCase(
        case_id=case_id,
        research_id=UUID("11111111-1111-4111-8111-111111111111"),
        claim="A research run stops after two planning iterations.",
        cited_semantic_ids=cited,
        gold_label=label,  # type: ignore[arg-type]
        expect_structurally_valid=valid,
    )


def _verdicts(dataset: object) -> dict[str, JudgeVerdict]:
    verdicts: dict[str, JudgeVerdict] = {}
    for case in dataset.grounding:  # type: ignore[attr-defined]
        prepared = prepare_claim(case, dataset.catalog)  # type: ignore[attr-defined]
        if isinstance(prepared, str):
            continue
        verdicts[case.case_id] = JudgeVerdict(
            verdict=case.gold_label,
            rationale="fixture label",
            evidence_ids=list(case.cited_semantic_ids),
        )
    return verdicts


def _scripted(dataset: object) -> ScriptedJudge:
    return ScriptedJudge(_verdicts(dataset))


def _report() -> ResearchReport:
    return ResearchReport(
        title="Limits",
        summary="A short summary that the judge must leave unchanged.",
        sections=[],
        limitations=[],
        status="insufficient_evidence",
        sources=[],
    )


def _result(
    gold: str,
    predicted: str | None,
    outcome: str,
) -> GroundingJudgeResult:
    return GroundingJudgeResult(
        case_id="g01-supported",
        gold_label=gold,  # type: ignore[arg-type]
        structural_valid=True,
        predicted_label=predicted,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        dataset_version=DATASET_VERSION,
    )
