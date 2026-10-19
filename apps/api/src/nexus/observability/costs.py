"""USD estimates from known quantities. Missing data is not treated as zero."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexus.core.config import Settings
from nexus.observability.pricing import (
    CHAT_PRICES,
    EMBEDDING_PRICES,
    PER_MILLION,
    PRICING_VERSION,
)
from nexus.observability.usage import OperationUsage, UsageSource

Coverage = Literal["complete", "partial", "unavailable"]
_BILLABLE = frozenset({"openai", "tavily"})
_EMBEDDING_OPERATIONS = frozenset({"embed_documents", "embed_query"})
_tavily_usd: Decimal | None = None


class CostLine(BaseModel):
    """One priced or unpriced billable observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    operation: str
    model: str | None = None
    amount: Decimal | None = None
    usage_source: UsageSource
    priced: bool


class CostEstimate(BaseModel):
    """A run total. ``complete`` is every billable line, not a vendor invoice."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: Literal["USD"] = "USD"
    amount: Decimal | None = None
    coverage: Coverage
    pricing_version: str = PRICING_VERSION
    breakdown: list[CostLine] = Field(default_factory=list)


def configure_cost_settings(settings: Settings) -> None:
    """Remember the optional Tavily unit price for this process."""
    global _tavily_usd
    _tavily_usd = settings.tavily_usd_per_search


def tavily_unit_price() -> Decimal | None:
    """The configured USD price of one Tavily search, if any."""
    return _tavily_usd


def reset_cost_settings() -> None:
    """Drop the process price. Tests use this to avoid leaking configuration."""
    global _tavily_usd
    _tavily_usd = None


def price_operations(
    records: list[OperationUsage],
    *,
    tavily_usd_per_search: Decimal | None,
) -> CostEstimate:
    """Price known chat, embedding, and search quantities."""
    lines: list[CostLine] = []
    priced_amounts: list[Decimal] = []
    any_estimated = False
    any_unpriced = False
    for record in records:
        if record.provider not in _BILLABLE:
            continue
        line = _price_line(record, tavily_usd_per_search)
        lines.append(line)
        if record.usage_source == "estimated":
            any_estimated = True
        if line.priced and line.amount is not None:
            priced_amounts.append(line.amount)
        else:
            any_unpriced = True
    if not lines or not priced_amounts:
        return CostEstimate(
            amount=None,
            coverage="unavailable",
            breakdown=lines,
        )
    amount = sum(priced_amounts, Decimal("0"))
    if any_unpriced or any_estimated:
        coverage: Coverage = "partial"
    else:
        coverage = "complete"
    return CostEstimate(amount=amount, coverage=coverage, breakdown=lines)


def _price_line(
    record: OperationUsage,
    tavily_usd_per_search: Decimal | None,
) -> CostLine:
    amount = _amount(record, tavily_usd_per_search)
    return CostLine(
        provider=record.provider,
        operation=record.operation,
        model=record.model,
        amount=amount,
        usage_source=record.usage_source,
        priced=amount is not None,
    )


def _amount(
    record: OperationUsage,
    tavily_usd_per_search: Decimal | None,
) -> Decimal | None:
    if record.provider == "openai" and record.operation == "openai_chat":
        return _chat_amount(record)
    if record.provider == "openai" and record.operation in _EMBEDDING_OPERATIONS:
        return _embedding_amount(record)
    if record.provider == "tavily" and record.operation == "web_search":
        return _search_amount(record, tavily_usd_per_search)
    return None


def _chat_amount(record: OperationUsage) -> Decimal | None:
    if record.usage_source != "provider":
        return None
    if record.input_tokens is None or record.output_tokens is None:
        return None
    if record.model is None:
        return None
    rates = CHAT_PRICES.get(record.model)
    if rates is None:
        return None
    input_rate, output_rate = rates
    input_cost = Decimal(record.input_tokens) * input_rate
    output_cost = Decimal(record.output_tokens) * output_rate
    return (input_cost + output_cost) / PER_MILLION


def _embedding_amount(record: OperationUsage) -> Decimal | None:
    if record.usage_source not in {"provider", "estimated"}:
        return None
    if record.embedding_tokens is None or record.model is None:
        return None
    rate = EMBEDDING_PRICES.get(record.model)
    if rate is None:
        return None
    return Decimal(record.embedding_tokens) * rate / PER_MILLION


def _search_amount(
    record: OperationUsage,
    tavily_usd_per_search: Decimal | None,
) -> Decimal | None:
    if tavily_usd_per_search is None or record.physical_attempts is None:
        return None
    if record.physical_attempts < 1:
        return None
    return tavily_usd_per_search * record.physical_attempts
