"""Recall, precision, and MRR on semantic ids. Empty gold is undefined."""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from nexus.evaluation.datasets import Catalog, EvaluationDataset, content_hash
from nexus.evaluation.schemas import (
    CorpusChunk,
    K,
    RetrievalAggregate,
    RetrievalCaseResult,
    RetrievalReport,
)
from nexus.retrieval.vector_store import EvidenceMatch, EvidencePoint

QUANT = Decimal("0.000001")


def dedupe(semantic_ids: Sequence[str]) -> list[str]:
    """Keep the first occurrence of each semantic id."""
    seen: set[str] = set()
    ordered: list[str] = []
    for semantic_id in semantic_ids:
        if semantic_id in seen:
            continue
        seen.add(semantic_id)
        ordered.append(semantic_id)
    return ordered


def rank_scored(pairs: Sequence[tuple[str, float]]) -> list[str]:
    """Order by score, then semantic id. The first score for an id wins."""
    scores: dict[str, float] = {}
    for semantic_id, score in pairs:
        if semantic_id in scores:
            continue
        scores[semantic_id] = score
    return sorted(scores, key=lambda semantic_id: (-scores[semantic_id], semantic_id))


def score_case(
    case_id: str,
    ranked: Sequence[str],
    gold: Sequence[str],
    *,
    k: int = K,
) -> RetrievalCaseResult:
    """Score one ordered list. An empty gold set leaves every metric null."""
    if k < 1:
        raise ValueError("k must be at least 1")
    gold_set = set(gold)
    if not gold_set:
        return RetrievalCaseResult(
            case_id=case_id,
            gold_count=0,
            recall_at_k=None,
            precision_at_k=None,
            mrr_at_k=None,
        )
    top = dedupe(ranked)[:k]
    hits = sum(1 for semantic_id in top if semantic_id in gold_set)
    reciprocal = Decimal(0)
    for index, semantic_id in enumerate(top, start=1):
        if semantic_id in gold_set:
            reciprocal = Decimal(1) / Decimal(index)
            break
    return RetrievalCaseResult(
        case_id=case_id,
        gold_count=len(gold_set),
        recall_at_k=_format(Decimal(hits) / Decimal(len(gold_set))),
        precision_at_k=_format(Decimal(hits) / Decimal(k)),
        mrr_at_k=_format(reciprocal),
    )


def macro_average(values: Sequence[str | None]) -> tuple[str | None, int, int]:
    """Average the defined values. Undefined entries are counted apart."""
    defined = [Decimal(value) for value in values if value is not None]
    undefined = len(values) - len(defined)
    if not defined:
        return None, 0, undefined
    total = sum(defined, Decimal(0))
    mean = total / Decimal(len(defined))
    return _format(mean), len(defined), undefined


def score_dataset(dataset: EvaluationDataset, *, k: int = K) -> RetrievalReport:
    """Score the frozen rank lists. The lists are already ordered."""
    cases = [
        score_case(case.case_id, dataset.ranks[case.case_id], case.gold, k=k)
        for case in sorted(dataset.queries, key=lambda item: item.case_id)
    ]
    recall, defined, undefined = macro_average([case.recall_at_k for case in cases])
    precision, _, _ = macro_average([case.precision_at_k for case in cases])
    mrr, _, _ = macro_average([case.mrr_at_k for case in cases])
    return RetrievalReport(
        k=k,
        cases=cases,
        aggregate=RetrievalAggregate(
            recall_at_k=recall,
            precision_at_k=precision,
            mrr_at_k=mrr,
            defined_cases=defined,
            undefined_cases=undefined,
        ),
    )


def ranks_from_matches(
    matches: Sequence[EvidenceMatch],
    catalog: Catalog,
) -> list[str]:
    """Map stored chunk ids back to semantic ids and rank those ids."""
    index = catalog.by_chunk_id()
    pairs: list[tuple[str, float]] = []
    for match in matches:
        semantic_id = index.get(match.chunk_id)
        if semantic_id is None:
            continue
        pairs.append((semantic_id, match.score))
    return rank_scored(pairs)


def evidence_point(
    chunk: CorpusChunk,
    vector: Sequence[float],
    *,
    claim_token: UUID,
) -> EvidencePoint:
    """Build the point the adapter would upsert for one fixture chunk."""
    return EvidencePoint(
        research_id=chunk.research_id,
        source_id=chunk.source_id,
        chunk_index=chunk.chunk_index,
        text=chunk.text,
        vector=vector,
        content_hash=content_hash(chunk.text),
        claim_token=claim_token,
        page_number=chunk.page_number,
        url=chunk.url,
        title=chunk.semantic_id,
    )


def _format(value: Decimal) -> str:
    return format(value.quantize(QUANT), "f")
