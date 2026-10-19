"""Per-operation observations. These are not the logical graph budget."""

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

UsageSource = Literal["provider", "estimated", "unavailable"]
OperationOutcome = Literal["ok", "error", "cancelled"]
BUFFER_LIMIT = 256
_CHAT_OPERATION = "openai_chat"
_EMBEDDING_OPERATIONS = frozenset({"embed_documents", "embed_query"})
_RUN_OPERATION = "research_run"


class OperationUsage(BaseModel):
    """One observed call. Unknown quantities stay null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID | None = None
    stage: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    model: str | None = None
    duration_ms: float = Field(ge=0)
    outcome: OperationOutcome
    logical_operations: int | None = Field(default=None, ge=0)
    physical_attempts: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    embedding_tokens: int | None = Field(default=None, ge=0)
    usage_source: UsageSource


class UsageBuffer:
    """A capped list of observations for one run."""

    def __init__(self, *, limit: int = BUFFER_LIMIT) -> None:
        self.records: list[OperationUsage] = []
        self.dropped = 0
        self.limit = limit

    def add(self, record: OperationUsage) -> None:
        """Keep the first records. Further observations increment ``dropped``."""
        if len(self.records) >= self.limit:
            self.dropped += 1
            return
        self.records.append(record)


def estimate_text_tokens(texts: Sequence[str], model: str) -> int | None:
    """Count tokens with tiktoken. Failure means the count is unavailable."""
    try:
        import tiktoken

        encoding = tiktoken.encoding_for_model(model)
        total = 0
        for text in texts:
            total += len(encoding.encode(text))
    except Exception:
        return None
    if total < 0:
        return None
    return total


def provider_token_counts(metadata: object) -> tuple[int | None, int | None]:
    """Read provider counts only when both values are present non-negative ints."""
    if not isinstance(metadata, dict):
        return None, None
    raw_input = metadata.get("input_tokens")
    raw_output = metadata.get("output_tokens")
    if type(raw_input) is not int or type(raw_output) is not int:
        return None, None
    if raw_input < 0 or raw_output < 0:
        return None, None
    # LangChain fills a missing usage object with zeros. That is not a measurement.
    if raw_input == 0 and raw_output == 0:
        return None, None
    return raw_input, raw_output


def sdk_attempt_count(max_retries: int) -> int | None:
    """One physical attempt is known only when the SDK retry loop is off."""
    if max_retries == 0:
        return 1
    return None


def record_usage(record: OperationUsage) -> None:
    """Store one observation. A telemetry failure is ignored."""
    try:
        from nexus.observability.context import current_buffer

        buffer = current_buffer()
        if buffer is None:
            return
        buffer.add(record)
    except Exception as exc:
        logger.warning("usage record failed: %s", type(exc).__name__)


def summary_fields(
    records: Sequence[OperationUsage],
    *,
    dropped: int,
    tavily_usd_per_search: Decimal | None,
) -> dict[str, object]:
    """Allowlisted run totals. Unknown chat sums are omitted, not reported as zero."""
    from nexus.observability.costs import price_operations, unknown_billable_count

    observed = list(records)
    estimate = price_operations(
        observed,
        tavily_usd_per_search=tavily_usd_per_search,
    )
    fields: dict[str, object] = {
        "stage": "run",
        "operation": "usage_summary",
        "outcome": "ok",
        "coverage": estimate.coverage,
        "pricing_version": estimate.pricing_version,
        "currency": estimate.currency,
        "dropped": dropped,
        "operation_count": len(observed),
        "unknown_operations": unknown_billable_count(
            observed,
            tavily_usd_per_search=tavily_usd_per_search,
        ),
    }
    if estimate.amount is not None:
        fields["estimated_usd"] = format(estimate.amount, "f")
    duration_ms = _run_duration_ms(observed)
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    chat_totals = _chat_totals(observed)
    if chat_totals is not None:
        fields["input_tokens"], fields["output_tokens"] = chat_totals
    embedding_tokens = _embedding_total(observed)
    if embedding_tokens is not None:
        fields["embedding_tokens"] = embedding_tokens
    return fields


def _run_duration_ms(records: Sequence[OperationUsage]) -> float | None:
    for record in records:
        if record.operation == _RUN_OPERATION:
            return record.duration_ms
    return None


def _chat_totals(records: Sequence[OperationUsage]) -> tuple[int, int] | None:
    chats = [
        record
        for record in records
        if record.provider == "openai" and record.operation == _CHAT_OPERATION
    ]
    if not chats or any(not _chat_measured(record) for record in chats):
        return None
    return (
        sum(record.input_tokens or 0 for record in chats),
        sum(record.output_tokens or 0 for record in chats),
    )


def _chat_measured(record: OperationUsage) -> bool:
    return (
        record.usage_source == "provider"
        and record.input_tokens is not None
        and record.output_tokens is not None
        and record.physical_attempts == 1
    )


def _embedding_total(records: Sequence[OperationUsage]) -> int | None:
    totals = [
        record.embedding_tokens
        for record in records
        if record.provider == "openai"
        and record.operation in _EMBEDDING_OPERATIONS
        and record.embedding_tokens is not None
    ]
    if not totals:
        return None
    return sum(totals)


def emit_run_summary() -> None:
    """Log one aggregate. The HTTP response is left unchanged."""
    try:
        from nexus.observability.context import current_buffer
        from nexus.observability.costs import tavily_unit_price
        from nexus.observability.logging import log_event

        buffer = current_buffer()
        if buffer is None:
            return
        log_event(
            "research.usage",
            **summary_fields(
                buffer.records,
                dropped=buffer.dropped,
                tavily_usd_per_search=tavily_unit_price(),
            ),
        )
    except Exception as exc:
        logger.warning("usage summary failed: %s", type(exc).__name__)
