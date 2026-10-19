"""Load the authored fixtures. Gold files never store Qdrant point ids."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from nexus.evaluation.schemas import (
    DATASET_VERSION,
    CitationCase,
    CorpusChunk,
    GroundingCase,
    QueryCase,
    RankCase,
)
from nexus.retrieval.ids import evidence_point_id

FIXTURE_CLAIM_TOKEN = UUID("33333333-3333-4333-8333-333333333333")


class DatasetError(Exception):
    """The fixture set failed schema or cross-file checks."""


class _Versioned(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str


class _CorpusFile(_Versioned):
    chunks: list[CorpusChunk]


class _QueryFile(_Versioned):
    cases: list[QueryCase]


class _RankFile(_Versioned):
    cases: list[RankCase]


class _CitationFile(_Versioned):
    cases: list[CitationCase]


class _GroundingFile(_Versioned):
    cases: list[GroundingCase]


@dataclass(frozen=True)
class Catalog:
    """Semantic ids for one fixture version, plus the adapter's claim token."""

    claim_token: UUID
    chunks: Mapping[str, CorpusChunk]

    def chunk_id(self, semantic_id: str) -> UUID:
        """Return the point id the retrieval adapter would write."""
        chunk = self.chunks[semantic_id]
        return evidence_point_id(
            chunk.source_id,
            content_hash(chunk.text),
            self.claim_token,
            chunk.chunk_index,
        )

    def by_chunk_id(self) -> dict[UUID, str]:
        """Map a stored point id back to the semantic gold key."""
        return {self.chunk_id(semantic_id): semantic_id for semantic_id in self.chunks}


@dataclass(frozen=True)
class EvaluationDataset:
    """Queries, frozen ranks, citations, and grounding claims."""

    version: str
    catalog: Catalog
    queries: tuple[QueryCase, ...]
    ranks: Mapping[str, tuple[str, ...]]
    citations: tuple[CitationCase, ...]
    grounding: tuple[GroundingCase, ...]


def default_fixture_dir() -> Path:
    """The fixtures shipped next to the API tests."""
    return Path(__file__).resolve().parents[3] / "tests" / "evaluation" / "fixtures"


def content_hash(text: str) -> str:
    """Digest passage text the same way an indexed source would."""
    return hashlib.sha256(text.encode()).hexdigest()


def load_dataset(directory: Path | None = None) -> EvaluationDataset:
    """Read the five fixture files. A schema problem raises ``DatasetError``."""
    root = default_fixture_dir() if directory is None else directory
    corpus = _read(root / "corpus.json", _CorpusFile)
    queries = _read(root / "queries.json", _QueryFile)
    ranks = _read(root / "retrieval_outputs.json", _RankFile)
    citations = _read(root / "citation_cases.json", _CitationFile)
    grounding = _read(root / "grounding_cases.json", _GroundingFile)
    for name, version in (
        ("corpus.json", corpus.dataset_version),
        ("queries.json", queries.dataset_version),
        ("retrieval_outputs.json", ranks.dataset_version),
        ("citation_cases.json", citations.dataset_version),
        ("grounding_cases.json", grounding.dataset_version),
    ):
        if version != DATASET_VERSION:
            raise DatasetError(f"{name}: unexpected dataset version")
    chunks = _chunks(corpus.chunks)
    catalog = Catalog(claim_token=FIXTURE_CLAIM_TOKEN, chunks=chunks)
    query_cases = _unique_cases(queries.cases, "queries.json")
    rank_map = _ranks(ranks.cases, query_cases, chunks)
    _known_gold(query_cases, chunks)
    return EvaluationDataset(
        version=DATASET_VERSION,
        catalog=catalog,
        queries=tuple(query_cases),
        ranks=rank_map,
        citations=tuple(_unique_cases(citations.cases, "citation_cases.json")),
        grounding=tuple(_unique_cases(grounding.cases, "grounding_cases.json")),
    )


def _read[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return model.model_validate(payload)
    except OSError, json.JSONDecodeError, ValidationError:
        raise DatasetError(f"{path.name}: invalid schema") from None


def _chunks(chunks: list[CorpusChunk]) -> dict[str, CorpusChunk]:
    found: dict[str, CorpusChunk] = {}
    for chunk in chunks:
        if chunk.semantic_id in found:
            raise DatasetError("corpus.json: duplicate semantic id")
        found[chunk.semantic_id] = chunk
    if not found:
        raise DatasetError("corpus.json: empty catalog")
    return found


class _HasCaseId(Protocol):
    case_id: str


def _unique_cases[CaseT: _HasCaseId](cases: list[CaseT], name: str) -> list[CaseT]:
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise DatasetError(f"{name}: duplicate case id")
        seen.add(case.case_id)
    if not seen:
        raise DatasetError(f"{name}: no cases")
    return cases


def _ranks(
    cases: list[RankCase],
    queries: list[QueryCase],
    chunks: Mapping[str, CorpusChunk],
) -> dict[str, tuple[str, ...]]:
    query_ids = {case.case_id for case in queries}
    found: dict[str, tuple[str, ...]] = {}
    for case in cases:
        if case.case_id in found:
            raise DatasetError("retrieval_outputs.json: duplicate case id")
        if case.case_id not in query_ids:
            raise DatasetError("retrieval_outputs.json: unknown case")
        for semantic_id in case.ranked:
            if semantic_id not in chunks:
                raise DatasetError("retrieval_outputs.json: unknown semantic id")
        found[case.case_id] = tuple(case.ranked)
    if set(found) != query_ids:
        raise DatasetError("retrieval_outputs.json: missing case")
    return found


def _known_gold(
    queries: list[QueryCase],
    chunks: Mapping[str, CorpusChunk],
) -> None:
    for case in queries:
        if len(case.gold) != len(set(case.gold)):
            raise DatasetError("queries.json: duplicate gold id")
        for semantic_id in case.gold:
            if semantic_id not in chunks:
                raise DatasetError("queries.json: unknown semantic id")
