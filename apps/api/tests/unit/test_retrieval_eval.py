"""The lexical baseline retrieves every labeled page in this fixture."""

from nexus.retrieval.evaluate import evaluate_window, load_fixture


def test_fixture_has_five_documents_and_ten_queries() -> None:
    documents, queries = load_fixture()
    assert len(documents) == 5
    assert len(queries) == 10
    labeled = {(query.document_id, query.page) for query in queries}
    pages = {
        (document.document_id, page)
        for document in documents
        for page, _text in enumerate(document.pages, start=1)
    }
    assert labeled == pages


def test_lexical_baseline_hits_every_labeled_page() -> None:
    """Hashed tokens, not an OpenAI embedding measurement."""
    production_hit, production_recall = evaluate_window(800, 120)
    alternate_hit, alternate_recall = evaluate_window(200, 40)

    assert production_hit == 1.0
    assert production_recall == 1.0
    assert alternate_hit == 1.0
    assert alternate_recall == 1.0
