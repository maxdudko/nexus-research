"""Optional OpenTelemetry. Disabled mode does not export or open a socket."""

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import ProxyTracerProvider, Tracer

from nexus import __version__
from nexus.core.config import Settings

logger = logging.getLogger(__name__)

_FLUSH_TIMEOUT_MS = 2000
_tracer_override: ContextVar[Tracer | None] = ContextVar(
    "nexus_tracer",
    default=None,
)
_configured: Tracer | None = None
_provider: trace.TracerProvider | None = None
_processor: object | None = None
_installed = False


def active_tracer() -> Tracer:
    """The test override, the configured tracer, or a non-recording tracer."""
    override = _tracer_override.get()
    if override is not None:
        return override
    if _configured is not None:
        return _configured
    return trace.get_tracer("nexus", __version__)


@contextmanager
def bind_tracer(tracer: Tracer) -> Iterator[None]:
    """Use a local tracer. This does not install a process-global provider."""
    token = _tracer_override.set(tracer)
    try:
        yield
    finally:
        _tracer_override.reset(token)


def provider_is_replaceable(provider: trace.TracerProvider) -> bool:
    """A host-installed provider is left in place."""
    return isinstance(provider, ProxyTracerProvider)


def configure_tracing(settings: Settings) -> None:
    """Install a provider only when tracing is enabled and nobody else has one."""
    global _configured, _provider, _processor, _installed
    if not settings.otel_enabled or _installed:
        return
    _installed = True
    current = trace.get_tracer_provider()
    if not provider_is_replaceable(current):
        _configured = trace.get_tracer("nexus", __version__)
        logger.warning("otel left the existing tracer provider in place")
        return
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: settings.otel_service_name}),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
    )
    endpoint = settings.otel_endpoint
    processor = None
    if endpoint is None:
        logger.warning("otel enabled without an endpoint; spans are not exported")
    else:
        processor = _exporter_processor(str(endpoint))
        if processor is not None:
            provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _provider = provider
    _processor = processor
    _configured = provider.get_tracer("nexus", __version__)


def shutdown_tracing() -> None:
    """Flush with a short bound. Exporter failures do not propagate."""
    processor = _processor
    provider = _provider
    if processor is not None:
        force_flush = getattr(processor, "force_flush", None)
        if callable(force_flush):
            try:
                force_flush(_FLUSH_TIMEOUT_MS)
            except Exception as exc:
                logger.warning("otel flush failed: %s", type(exc).__name__)
        batch = getattr(processor, "_batch_processor", None)
        shutdown = getattr(batch, "shutdown", None) if batch is not None else None
        try:
            if callable(shutdown):
                shutdown(timeout_millis=_FLUSH_TIMEOUT_MS)
            else:
                shutdown_processor = getattr(processor, "shutdown", None)
                if callable(shutdown_processor):
                    shutdown_processor()
        except Exception as exc:
            logger.warning("otel shutdown failed: %s", type(exc).__name__)
    if provider is not None:
        try:
            shutdown_provider = getattr(provider, "shutdown", None)
            if callable(shutdown_provider):
                shutdown_provider()
        except Exception as exc:
            logger.warning("otel provider shutdown failed: %s", type(exc).__name__)


def reset_tracing_for_tests() -> None:
    """Forget this process's provider handle. Does not touch the global one."""
    global _configured, _provider, _processor, _installed
    _configured = None
    _provider = None
    _processor = None
    _installed = False


def _exporter_processor(endpoint: str) -> SpanProcessor | None:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        return BatchSpanProcessor(
            _SafeExporter(OTLPSpanExporter(endpoint=endpoint, timeout=5))
        )
    except Exception as exc:
        logger.warning("otel exporter was not configured: %s", type(exc).__name__)
        return None


class _SafeExporter(SpanExporter):
    """Swallow exporter errors so a collector cannot fail a research run."""

    def __init__(self, inner: SpanExporter) -> None:
        super().__init__()
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            return self._inner.export(spans)
        except Exception as exc:
            logger.warning("otel export failed: %s", type(exc).__name__)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception as exc:
            logger.warning("otel exporter shutdown failed: %s", type(exc).__name__)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception as exc:
            logger.warning("otel exporter flush failed: %s", type(exc).__name__)
        return False
