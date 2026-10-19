"""Per-operation observations. These are not the logical graph budget."""

import logging
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

UsageSource = Literal["provider", "estimated", "unavailable"]
OperationOutcome = Literal["ok", "error", "cancelled"]
BUFFER_LIMIT = 256


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
    return raw_input, raw_output


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


def emit_run_summary() -> None:
    """Log one aggregate. The HTTP response is left unchanged."""
    try:
        from nexus.observability.context import current_buffer
        from nexus.observability.costs import price_operations, tavily_unit_price
        from nexus.observability.logging import log_event

        buffer = current_buffer()
        if buffer is None:
            return
        estimate = price_operations(
            buffer.records,
            tavily_usd_per_search=tavily_unit_price(),
        )
        fields: dict[str, object] = {
            "stage": "run",
            "operation": "usage_summary",
            "outcome": "ok",
            "coverage": estimate.coverage,
            "pricing_version": estimate.pricing_version,
            "currency": estimate.currency,
            "dropped": buffer.dropped,
            "operation_count": len(buffer.records),
        }
        if estimate.amount is not None:
            fields["estimated_usd"] = format(estimate.amount, "f")
        log_event("research.usage", **fields)
    except Exception as exc:
        logger.warning("usage summary failed: %s", type(exc).__name__)
