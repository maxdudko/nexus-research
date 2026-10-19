"""Hand-calculated retrieval metrics and the frozen fixture list."""

import uuid
from pathlib import Path

from nexus.evaluation.datasets import default_fixture_dir, load_dataset
from nexus.evaluation.retrieval import (
    dedupe,
    macro_average,
    rank_scored,
    ranks_from_matches,
    score_case,
    score_dataset,
)
from nexus.evaluation.runner import run_offline
from nexus.evaluation.schemas import CorpusChunk
from nexus.retrieval.vector_store import EvidenceMatch

EXPECTED = {
    "q01-single": ("1.000000", "0.200000", "1.000000"),
    "q02-multi": ("1.000000", "0.600000", "1.000000"),
    "q03-paraphrase": ("1.000000", "0.400000", "0.500000"),
    "q04-no-gold": (None, None, None),
    "q05-miss": ("0.000000", "0.000000", "0.000000"),
    "q06-short": ("1.000000", "0.200000", "1.000000"),
    "q07-duplicate": ("1.000000", "0.200000", "1.000000"),
    "q08-rank3": ("1.000000", "0.200000", "0.333333"),
    "q09-half": ("0.500000", "0.200000", "1.000000"),
    "q10-pdf": ("1.000000", "0.200000", "1.000000"),
    "q11-web": ("1.000000", "0.200000", "1.000000"),
    "q12-past-k": ("0.000000", "0.000000", "0.000000"),
    "q13-both-pdf": ("1.000000", "0.400000", "1.000000"),
    "q14-late-pair": ("1.000000", "0.400000", "0.250000"),
    "q15-noise-hit": ("1.000000", "0.200000", "1.000000"),
    "q16-second-only": ("1.000000", "0.200000", "0.500000"),
}


def test_empty_gold_is_undefined() -> None:
    result = score_case("empty", ["a"], [])

    assert result.gold_count == 0
    assert result.recall_at_k is None
    assert result.precision_at_k is None
    assert result.mrr_at_k is None


def test_duplicate_id_does_not_add_another_hit() -> None:
    result = score_case("dup", ["a", "a", "b"], ["a"])

    assert dedupe(["a", "a", "b"]) == ["a", "b"]
    assert result.recall_at_k == "1.000000"
    assert result.precision_at_k == "0.200000"
    assert result.mrr_at_k == "1.000000"


def test_short_list_keeps_precision_denominator_at_k() -> None:
    result = score_case("short", ["a"], ["a"])

    assert result.precision_at_k == "0.200000"
    assert result.recall_at_k == "1.000000"
    assert result.mrr_at_k == "1.000000"


def test_mrr_uses_the_first_relevant_rank_inside_k() -> None:
    inside = score_case("inside", ["b", "c", "a"], ["a"])
    outside = score_case("outside", ["b", "c", "d", "e", "f", "a"], ["a"])

    assert inside.mrr_at_k == "0.333333"
    assert inside.recall_at_k == "1.000000"
    assert outside.recall_at_k == "0.000000"
    assert outside.precision_at_k == "0.000000"
    assert outside.mrr_at_k == "0.000000"


def test_partial_recall_counts_every_gold_id() -> None:
    result = score_case("half", ["b", "c"], ["a", "b"])

    assert result.recall_at_k == "0.500000"
    assert result.precision_at_k == "0.200000"
    assert result.mrr_at_k == "1.000000"


def test_equal_scores_sort_by_semantic_id_and_the_first_score_wins() -> None:
    assert rank_scored([("b", 1.0), ("a", 1.0)]) == ["a", "b"]
    assert rank_scored([("a", 0.1), ("a", 0.9), ("b", 0.5)]) == ["b", "a"]


def test_macro_average_skips_undefined_cases() -> None:
    assert macro_average([None, "1.000000", "0.000000"]) == ("0.500000", 2, 1)
    assert macro_average([None, None]) == (None, 0, 2)


def test_fixture_metrics_match_the_hand_calculation() -> None:
    report = score_dataset(load_dataset())

    assert [case.case_id for case in report.cases] == sorted(EXPECTED)
    for case in report.cases:
        recall, precision, mrr = EXPECTED[case.case_id]
        assert case.recall_at_k == recall
        assert case.precision_at_k == precision
        assert case.mrr_at_k == mrr
    assert report.aggregate.recall_at_k == "0.833333"
    assert report.aggregate.precision_at_k == "0.240000"
    assert report.aggregate.mrr_at_k == "0.705556"
    assert report.aggregate.defined_cases == 15
    assert report.aggregate.undefined_cases == 1


def test_chunk_ids_are_stable_and_absent_from_the_gold_files() -> None:
    dataset = load_dataset()
    point_id = dataset.catalog.chunk_id("web-limits#c0")

    assert load_dataset().catalog.chunk_id("web-limits#c0") == point_id
    for path in Path(default_fixture_dir()).glob("*.json"):
        assert str(point_id) not in path.read_text(encoding="utf-8")


def test_match_chunk_ids_map_back_to_semantic_ids() -> None:
    dataset = load_dataset()
    catalog = dataset.catalog
    known = catalog.chunks["web-limits#c0"]
    other = catalog.chunks["web-budget#c0"]
    matches = [
        _match(catalog.chunk_id("web-budget#c0"), other, 0.4),
        _match(catalog.chunk_id("web-limits#c0"), known, 0.4),
        _match(uuid.uuid4(), known, 0.9),
    ]

    assert ranks_from_matches(matches, catalog) == ["web-budget#c0", "web-limits#c0"]


def test_zero_recall_does_not_fail_the_offline_suite() -> None:
    report = run_offline(load_dataset())
    missed = next(case for case in report.retrieval.cases if case.case_id == "q05-miss")

    assert missed.recall_at_k == "0.000000"
    assert report.suite == "offline"


def _match(chunk_id: uuid.UUID, chunk: CorpusChunk, score: float) -> EvidenceMatch:
    return EvidenceMatch(
        chunk_id=chunk_id,
        source_id=chunk.source_id,
        research_id=chunk.research_id,
        score=score,
        text=chunk.text,
        page_number=None,
        url="https://example.test/limits",
        title=None,
        content_hash="a" * 64,
        chunk_index=0,
    )
