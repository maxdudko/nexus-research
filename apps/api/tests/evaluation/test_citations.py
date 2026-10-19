"""Citation provenance stays separate from the support label."""

from nexus.evaluation.citations import (
    check_citation,
    check_citations,
    provenance_matches,
)
from nexus.evaluation.datasets import EvaluationDataset, load_dataset


def test_web_rows_have_a_url_and_pdf_rows_have_a_page() -> None:
    assert provenance_matches("web", "https://example.test/a", None)
    assert not provenance_matches("web", None, 1)
    assert not provenance_matches("web", "https://example.test/a", 2)
    assert provenance_matches("pdf", None, 2)
    assert not provenance_matches("pdf", "https://example.test/a", 2)
    assert not provenance_matches("pdf", None, None)


def test_a_resolvable_citation_can_still_be_unsupported() -> None:
    dataset = load_dataset()
    result = _check(dataset, "c02-spurious")

    assert result.citation_valid is True
    assert result.support_label == "unsupported"
    assert result.error_codes == []


def test_missing_evidence_fails_provenance() -> None:
    result = _check(load_dataset(), "c03-missing")

    assert result.citation_valid is False
    assert "missing_evidence" in result.error_codes


def test_foreign_research_id_fails_provenance() -> None:
    dataset = load_dataset()

    foreign = _check(dataset, "c04-foreign")
    scope = _check(dataset, "c10-scope")

    assert foreign.citation_valid is False
    assert "foreign_research" in foreign.error_codes
    assert scope.citation_valid is False
    assert "foreign_research" in scope.error_codes


def test_malformed_duplicate_and_unknown_citations_fail() -> None:
    dataset = load_dataset()

    malformed = _check(dataset, "c05-malformed")
    unknown = _check(dataset, "c08-unknown")
    duplicate = _check(dataset, "c09-duplicate")

    assert malformed.citation_valid is False
    assert "malformed_id" in malformed.error_codes
    assert unknown.citation_valid is False
    assert "unknown_source" in unknown.error_codes
    assert duplicate.citation_valid is False
    assert "duplicate_id" in duplicate.error_codes


def test_pdf_and_web_rows_resolve() -> None:
    dataset = load_dataset()

    pdf = _check(dataset, "c06-pdf")
    web = _check(dataset, "c07-web")
    supported = _check(dataset, "c01-supported")

    assert pdf.citation_valid is True
    assert web.citation_valid is True
    assert supported.citation_valid is True
    assert supported.support_label == "supported"


def test_fixture_expectations_match_the_checker() -> None:
    report = check_citations(load_dataset())

    assert [case.case_id for case in report.cases] == sorted(
        case.case_id for case in report.cases
    )
    assert all(
        case.citation_valid == case.expect_citation_valid for case in report.cases
    )


def _check(dataset: EvaluationDataset, case_id: str):
    case = next(item for item in dataset.citations if item.case_id == case_id)
    return check_citation(case, dataset.catalog)
