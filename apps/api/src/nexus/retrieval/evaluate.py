"""Lexical Hit@5 and Recall@5 for the committed retrieval fixture.

The vectors hash tokens. They are not OpenAI embeddings, and the scores are
not the quality of ``text-embedding-3-small``. Chunk settings are arguments,
so this module does not change the collection contract.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from nexus.ingestion.chunking import Chunker, normalize_text

_FIXTURE = Path(__file__).resolve().parent / "data" / "retrieval_queries.json"
_WIDTH = 256
_TOP_K = 5


@dataclass(frozen=True)
class EvalDocument:
    """One labeled document. Pages are 1-based in order."""

    document_id: str
    pages: tuple[str, ...]


@dataclass(frozen=True)
class EvalQuery:
    """A query labeled with the page that should be retrieved."""

    query: str
    document_id: str
    page: int


@dataclass(frozen=True)
class _Passage:
    document_id: str
    page: int
    vector: tuple[float, ...]


def load_fixture() -> tuple[tuple[EvalDocument, ...], tuple[EvalQuery, ...]]:
    """Load the five-document, ten-query fixture."""
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    documents = tuple(
        EvalDocument(document_id=item["id"], pages=tuple(item["pages"]))
        for item in raw["documents"]
    )
    queries = tuple(
        EvalQuery(
            query=item["query"],
            document_id=item["document_id"],
            page=item["page"],
        )
        for item in raw["queries"]
    )
    return documents, queries


def lexical_vector(text: str) -> list[float]:
    """Hash whitespace tokens into one normalized vector."""
    values = [0.0] * _WIDTH
    for token in normalize_text(text).lower().split():
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % _WIDTH
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        values[index] += sign
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


def evaluate_window(chunk_tokens: int, overlap_tokens: int) -> tuple[float, float]:
    """Return mean Hit@5 and mean Recall@5 for one chunk window."""
    documents, queries = load_fixture()
    chunker = Chunker(
        chunk_tokens=chunk_tokens,
        overlap_tokens=overlap_tokens,
        max_chunks=200,
        model_name="text-embedding-3-small",
    )
    passages: list[_Passage] = []
    for document in documents:
        pages = list(enumerate(document.pages, start=1))
        for chunk in chunker.chunk_pages(pages):
            if chunk.page_number is None:
                continue
            passages.append(
                _Passage(
                    document_id=document.document_id,
                    page=chunk.page_number,
                    vector=tuple(lexical_vector(chunk.text)),
                ),
            )
    hits = 0
    recall = 0.0
    for query in queries:
        query_vector = lexical_vector(query.query)
        ranked = sorted(
            passages,
            key=lambda passage: _dot(query_vector, passage.vector),
            reverse=True,
        )[:_TOP_K]
        relevant = [
            passage
            for passage in passages
            if passage.document_id == query.document_id and passage.page == query.page
        ]
        found = [
            passage
            for passage in ranked
            if passage.document_id == query.document_id and passage.page == query.page
        ]
        if found:
            hits += 1
        recall += len(found) / len(relevant)
    count = len(queries)
    return hits / count, recall / count


def _dot(left: list[float] | tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(item * other for item, other in zip(left, right, strict=True))
