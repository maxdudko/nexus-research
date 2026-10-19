"""Cost coverage follows known quantities and never invents a zero total."""

from decimal import Decimal

from nexus.observability.costs import price_operations
from nexus.observability.pricing import PRICING_VERSION
from nexus.observability.usage import OperationUsage
from nexus.schemas.report import GraphUsage


def _chat(
    *,
    model: str = "gpt-4o-mini",
    input_tokens: int | None = 1000,
    output_tokens: int | None = 1000,
    source: str = "provider",
) -> OperationUsage:
    return OperationUsage(
        stage="plan",
        provider="openai",
        operation="openai_chat",
        model=model,
        duration_ms=1,
        outcome="ok",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usage_source=source,  # type: ignore[arg-type]
    )


def _embedding(*, tokens: int | None, source: str = "estimated") -> OperationUsage:
    return OperationUsage(
        stage="retrieve",
        provider="openai",
        operation="embed_query",
        model="text-embedding-3-small",
        duration_ms=1,
        outcome="ok",
        embedding_tokens=tokens,
        usage_source=source,  # type: ignore[arg-type]
    )


def _search(attempts: int | None = 1) -> OperationUsage:
    return OperationUsage(
        stage="discover",
        provider="tavily",
        operation="web_search",
        duration_ms=1,
        outcome="ok",
        physical_attempts=attempts,
        usage_source="unavailable",
    )


def test_graph_usage_shape_is_unchanged() -> None:
    usage = GraphUsage(
        iterations=1,
        search_calls=2,
        llm_calls=3,
        pages_indexed=4,
        pages_failed=0,
    )

    assert usage.model_dump() == {
        "iterations": 1,
        "search_calls": 2,
        "llm_calls": 3,
        "pages_indexed": 4,
        "pages_failed": 0,
    }


def test_provider_chat_price_can_be_complete() -> None:
    estimate = price_operations([_chat()], tavily_usd_per_search=None)

    assert estimate.pricing_version == PRICING_VERSION
    assert estimate.currency == "USD"
    assert estimate.coverage == "complete"
    assert estimate.amount == Decimal("0.00075")


def test_missing_tokens_are_not_a_zero_total() -> None:
    estimate = price_operations(
        [_chat(input_tokens=None, output_tokens=None, source="unavailable")],
        tavily_usd_per_search=None,
    )

    assert estimate.amount is None
    assert estimate.coverage == "unavailable"
    assert estimate.amount != Decimal("0")


def test_unknown_model_price_is_unavailable() -> None:
    estimate = price_operations(
        [_chat(model="gpt-unknown")],
        tavily_usd_per_search=None,
    )

    assert estimate.amount is None
    assert estimate.coverage == "unavailable"


def test_partial_total_omits_unpriced_lines() -> None:
    estimate = price_operations(
        [_chat(), _chat(model="gpt-unknown")],
        tavily_usd_per_search=None,
    )

    assert estimate.coverage == "partial"
    assert estimate.amount == Decimal("0.00075")


def test_estimated_embeddings_are_never_complete() -> None:
    tokens = 500_000
    estimate = price_operations(
        [_embedding(tokens=tokens)],
        tavily_usd_per_search=None,
    )

    assert estimate.coverage == "partial"
    assert estimate.amount == Decimal(tokens) * Decimal("0.02") / Decimal("1000000")


def test_estimated_embeddings_keep_a_priced_chat_partial() -> None:
    estimate = price_operations(
        [_chat(), _embedding(tokens=1_000_000)],
        tavily_usd_per_search=None,
    )

    assert estimate.coverage == "partial"
    assert estimate.amount == Decimal("0.00075") + Decimal("0.02")


def test_tavily_without_a_unit_price_is_unavailable() -> None:
    estimate = price_operations([_search()], tavily_usd_per_search=None)

    assert estimate.amount is None
    assert estimate.coverage == "unavailable"


def test_tavily_unit_price_uses_physical_attempts() -> None:
    estimate = price_operations(
        [_search(), _search()],
        tavily_usd_per_search=Decimal("0.008"),
    )

    assert estimate.coverage == "complete"
    assert estimate.amount == Decimal("0.016")


def test_unpriced_search_makes_a_priced_chat_partial() -> None:
    estimate = price_operations(
        [_chat(), _search()],
        tavily_usd_per_search=None,
    )

    assert estimate.coverage == "partial"
    assert estimate.amount == Decimal("0.00075")


def test_non_billable_spans_do_not_invent_a_cost() -> None:
    record = OperationUsage(
        stage="retrieve",
        provider="qdrant",
        operation="qdrant_search",
        duration_ms=2,
        outcome="ok",
        usage_source="unavailable",
    )

    estimate = price_operations([record], tavily_usd_per_search=None)

    assert estimate.amount is None
    assert estimate.coverage == "unavailable"
    assert estimate.breakdown == []
