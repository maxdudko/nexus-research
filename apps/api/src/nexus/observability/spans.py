"""Allowlisted spans. Closing a span cannot change the caller's outcome."""

import asyncio
import logging
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from nexus.observability.context import current_context
from nexus.observability.logging import log_event, safe_token
from nexus.observability.tracing import active_tracer
from nexus.observability.usage import (
    OperationOutcome,
    OperationUsage,
    UsageSource,
    provider_token_counts,
    record_usage,
)

logger = logging.getLogger(__name__)


@dataclass
class OperationHandle:
    """Mutable fields read when the span closes."""

    stage: str
    operation: str
    provider: str
    model: str | None = None
    iteration: int | None = None
    outcome: str | None = None
    error_code: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    embedding_tokens: int | None = None
    usage_source: UsageSource = "unavailable"
    physical_attempts: int | None = None
    logical_operations: int | None = None

    def observe_tokens(
        self,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """Record provider chat counts. A missing side stays unavailable."""
        if input_tokens is None or output_tokens is None:
            return
        if type(input_tokens) is not int or type(output_tokens) is not int:
            return
        if input_tokens < 0 or output_tokens < 0:
            return
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.usage_source = "provider"

    def observe_embedding(self, tokens: int | None) -> None:
        """Record an explicit estimate. Provider embedding counts are not invented."""
        if type(tokens) is not int or tokens < 0:
            return
        self.embedding_tokens = tokens
        self.usage_source = "estimated"

    def observe_message(self, message: object) -> None:
        """Read ``usage_metadata`` when the provider left it on the message."""
        metadata = getattr(message, "usage_metadata", None)
        self.observe_tokens(*provider_token_counts(metadata))


@contextmanager
def operation(
    *,
    operation: str,
    provider: str,
    stage: str | None = None,
    model: str | None = None,
) -> Iterator[OperationHandle]:
    """Time one boundary. Setup failures still run the body."""
    context = current_context()
    handle = OperationHandle(
        stage=safe_token(stage or context.stage) or "unscoped",
        operation=safe_token(operation) or "operation",
        provider=safe_token(provider) or "unknown",
        model=safe_token(model),
        iteration=context.iteration if _non_negative(context.iteration) else None,
    )
    started = time.perf_counter()
    span, token = _open_span(handle.operation)
    try:
        yield handle
    except BaseException as exc:
        _finish(handle, exc, started, span, token)
        raise
    else:
        _finish(handle, None, started, span, token)


def _open_span(name: str) -> tuple[trace.Span | None, object | None]:
    try:
        span = active_tracer().start_span(name)
        token = otel_context.attach(trace.set_span_in_context(span))
        return span, token
    except Exception:
        return None, None


def _finish(
    handle: OperationHandle,
    exc: BaseException | None,
    started: float,
    span: trace.Span | None,
    token: object | None,
) -> None:
    try:
        outcome = _outcome(exc, handle)
        error_code = _error_code(exc, handle)
        duration_ms = _duration_ms(started)
        _write_span(span, handle, outcome, error_code, duration_ms)
        _remember(handle, outcome, error_code, duration_ms)
        log_event(
            "operation",
            stage=handle.stage,
            operation=handle.operation,
            provider=handle.provider,
            model=handle.model,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
            iteration=handle.iteration,
            usage_source=handle.usage_source,
            input_tokens=handle.input_tokens,
            output_tokens=handle.output_tokens,
            embedding_tokens=handle.embedding_tokens,
            physical_attempts=handle.physical_attempts,
            logical_operations=handle.logical_operations,
        )
        if span is not None and span.is_recording() and outcome != "ok":
            span.set_status(Status(StatusCode.ERROR))
    except Exception as close_error:
        _warn(close_error)
    finally:
        if span is not None:
            try:
                span.end()
            except Exception as end_error:
                _warn(end_error)
        if token is not None:
            try:
                otel_context.detach(token)  # type: ignore[arg-type]
            except Exception as detach_error:
                _warn(detach_error)


def _remember(
    handle: OperationHandle,
    outcome: OperationOutcome,
    error_code: str | None,
    duration_ms: float,
) -> None:
    del error_code
    context = current_context()
    record_usage(
        OperationUsage(
            run_id=context.run_id,
            stage=handle.stage,
            provider=handle.provider,
            operation=handle.operation,
            model=handle.model,
            duration_ms=duration_ms,
            outcome=outcome,
            logical_operations=handle.logical_operations,
            physical_attempts=handle.physical_attempts,
            input_tokens=handle.input_tokens,
            output_tokens=handle.output_tokens,
            embedding_tokens=handle.embedding_tokens,
            usage_source=handle.usage_source,
        )
    )


def _write_span(
    span: trace.Span | None,
    handle: OperationHandle,
    outcome: str,
    error_code: str | None,
    duration_ms: float,
) -> None:
    if span is None or not span.is_recording():
        return
    context = current_context()
    _attribute(span, "nexus.stage", handle.stage)
    _attribute(span, "nexus.operation", handle.operation)
    _attribute(span, "nexus.outcome", outcome)
    _attribute(span, "nexus.provider", handle.provider)
    _attribute(span, "duration_ms", duration_ms)
    if context.run_id is not None:
        _attribute(span, "nexus.run_id", str(context.run_id))
    if context.research_id is not None:
        _attribute(span, "nexus.research_id", str(context.research_id))
    if handle.iteration is not None:
        _attribute(span, "nexus.iteration", handle.iteration)
    if error_code is not None:
        _attribute(span, "nexus.error_code", error_code)
    if handle.model is not None:
        _attribute(span, "nexus.model", handle.model)
    _attribute(span, "nexus.usage_source", handle.usage_source)
    _attribute(span, "nexus.input_tokens", handle.input_tokens)
    _attribute(span, "nexus.output_tokens", handle.output_tokens)
    _attribute(span, "nexus.embedding_tokens", handle.embedding_tokens)
    _attribute(span, "nexus.physical_attempts", handle.physical_attempts)


def _attribute(span: trace.Span, key: str, value: object) -> None:
    if isinstance(value, str):
        token: str | None
        if key in {"nexus.run_id", "nexus.research_id"}:
            token = value
        else:
            token = safe_token(value)
        if token is None or "://" in token or "?" in token:
            return
        span.set_attribute(key, token)
        return
    if type(value) is int and value >= 0:
        span.set_attribute(key, value)
        return
    if type(value) is float and math.isfinite(value) and value >= 0:
        span.set_attribute(key, value)


def _outcome(exc: BaseException | None, handle: OperationHandle) -> OperationOutcome:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if exc is not None:
        return "error"
    chosen = handle.outcome
    if chosen == "ok":
        return "ok"
    if chosen == "error":
        return "error"
    if chosen == "cancelled":
        return "cancelled"
    return "ok"


def _error_code(exc: BaseException | None, handle: OperationHandle) -> str | None:
    chosen = safe_token(handle.error_code)
    if chosen is not None:
        return chosen
    if isinstance(exc, asyncio.CancelledError):
        return "interrupted"
    return safe_token(getattr(exc, "code", None))


def _duration_ms(started: float) -> float:
    elapsed = (time.perf_counter() - started) * 1000.0
    if not math.isfinite(elapsed) or elapsed < 0:
        return 0.0
    return elapsed


def _non_negative(value: int | None) -> bool:
    return type(value) is int and value >= 0


def _warn(exc: Exception) -> None:
    logger.warning("operation close failed: %s", type(exc).__name__)
