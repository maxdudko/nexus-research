"""Citation provenance. A resolvable label can still be unsupported."""

import re
from collections.abc import Sequence

from pydantic import ValidationError

from nexus.evaluation.datasets import Catalog, EvaluationDataset
from nexus.evaluation.schemas import (
    CitationCase,
    CitationCheck,
    CitationReport,
    CorpusChunk,
)
from nexus.schemas.report import EvidenceRef

_EVIDENCE_ID = re.compile(r"^E[1-9][0-9]*$")


def provenance_matches(kind: str, url: str | None, page: int | None) -> bool:
    """Web rows have a URL and no page. PDF rows have a page and no URL."""
    if kind == "web":
        return url is not None and page is None
    if kind == "pdf":
        return page is not None and url is None
    return False


def check_citation(case: CitationCase, catalog: Catalog) -> CitationCheck:
    """Resolve labels inside one research scope. Support is copied through."""
    errors: list[str] = []
    source_ids = [source.evidence_id for source in case.sources]
    if len(source_ids) != len(set(source_ids)) or len(case.citations) != len(
        set(case.citations)
    ):
        errors.append("duplicate_id")
    if set(case.citations) != set(source_ids):
        errors.append("missing_evidence")
    for evidence_id in [*source_ids, *case.citations]:
        if _EVIDENCE_ID.fullmatch(evidence_id) is None:
            errors.append("malformed_id")
    resolved: list[CorpusChunk] = []
    for source in case.sources:
        chunk = catalog.chunks.get(source.semantic_id)
        if chunk is None:
            errors.append("unknown_source")
            continue
        if chunk.research_id != case.research_id:
            errors.append("foreign_research")
        if not provenance_matches(chunk.kind, chunk.url, chunk.page_number):
            errors.append("provenance_mismatch")
        resolved.append(chunk)
    if not errors:
        try:
            _refs(case, resolved, catalog)
        except ValidationError:
            errors.append("provenance_mismatch")
    return CitationCheck(
        case_id=case.case_id,
        citation_valid=not errors,
        support_label=case.support_label,
        expect_citation_valid=case.expect_citation_valid,
        error_codes=_unique(errors),
    )


def check_citations(dataset: EvaluationDataset) -> CitationReport:
    """Check every citation case. Ordering follows the case id."""
    cases = [
        check_citation(case, dataset.catalog)
        for case in sorted(dataset.citations, key=lambda item: item.case_id)
    ]
    return CitationReport(cases=cases)


def _refs(
    case: CitationCase,
    chunks: Sequence[CorpusChunk],
    catalog: Catalog,
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for source, chunk in zip(case.sources, chunks, strict=True):
        refs.append(
            EvidenceRef(
                evidence_id=source.evidence_id,
                source_id=str(chunk.source_id),
                chunk_id=str(catalog.chunk_id(chunk.semantic_id)),
                research_id=str(case.research_id),
                url=chunk.url,
                page_number=chunk.page_number,
            )
        )
    return refs


def _unique(codes: list[str]) -> list[str]:
    ordered: list[str] = []
    for code in sorted(codes):
        if code not in ordered:
            ordered.append(code)
    return ordered
