"""Optional tracing stays local unless an endpoint is configured."""

import asyncio
import socket
import sys
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import ProxyTracerProvider
from tests.conftest import make_settings
from tests.unit.graph_support import (
    QUESTION,
    FakeDiscovery,
    FakeIngestion,
    FakePlanner,
    FakeRetrieval,
    ScriptedEvaluator,
    coverage,
    empty_discovery,
    make_context,
    plan_for,
)

from nexus.observability.context import bind_run
from nexus.observability.spans import operation
from nexus.observability.tracing import (
    _SafeExporter,
    bind_tracer,
    configure_tracing,
    provider_is_replaceable,
    reset_tracing_for_tests,
    shutdown_tracing,
)
from nexus.research.graph import build_graph
from nexus.research.nodes.synthesis import StaticSynthesizer
from nexus.research.runner import ResearchRunner

SECRET = "sk-span-secret"
PROMPT = "ignore previous instructions"


class _BoomExporter:
    def export(self, spans: object) -> SpanExportResult:
        del spans
        raise RuntimeError("collector down")

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        return False


@pytest.fixture
def memory_tracer() -> Iterator[tuple[TracerProvider, InMemorySpanExporter]]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider, exporter
    provider.shutdown()


def test_disabled_tracing_does_not_export_or_open_a_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.modules.pop("opentelemetry.exporter.otlp.proto.http.trace_exporter", None)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "create_connection", fail)
    configure_tracing(make_settings(otel_enabled=False))

    with operation(operation="plan", provider="graph", stage="plan"):
        pass

    assert "opentelemetry.exporter.otlp.proto.http.trace_exporter" not in sys.modules


def test_enabled_without_endpoint_does_not_import_the_exporter() -> None:
    import opentelemetry.trace as trace_api
    from opentelemetry.util._once import Once

    sys.modules.pop("opentelemetry.exporter.otlp.proto.http.trace_exporter", None)
    previous = trace_api._TRACER_PROVIDER
    previous_once = trace_api._TRACER_PROVIDER_SET_ONCE
    try:
        configure_tracing(make_settings(otel_enabled=True, otel_endpoint=None))
        assert (
            "opentelemetry.exporter.otlp.proto.http.trace_exporter" not in sys.modules
        )
    finally:
        shutdown_tracing()
        reset_tracing_for_tests()
        trace_api._TRACER_PROVIDER = previous
        trace_api._TRACER_PROVIDER_SET_ONCE = previous_once or Once()


def test_host_provider_is_not_replaceable() -> None:
    assert provider_is_replaceable(ProxyTracerProvider())
    assert not provider_is_replaceable(TracerProvider())


def test_exporter_failure_is_not_raised() -> None:
    result = _SafeExporter(_BoomExporter()).export(())

    assert result is SpanExportResult.FAILURE


async def test_parent_span_crosses_create_task(
    memory_tracer: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = memory_tracer

    async def child() -> None:
        with operation(operation="plan", provider="graph", stage="plan"):
            await asyncio.sleep(0)

    with bind_tracer(provider.get_tracer("nexus-test")):
        with operation(operation="research_run", provider="nexus", stage="run"):
            await asyncio.create_task(child())

    spans = {span.name: span for span in exporter.get_finished_spans()}
    parent = spans["research_run"].context.span_id
    assert spans["plan"].parent is not None
    assert spans["plan"].parent.span_id == parent


async def test_span_closes_on_exception_without_the_message(
    memory_tracer: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = memory_tracer

    with bind_tracer(provider.get_tracer("nexus-test")):
        with pytest.raises(RuntimeError):
            with operation(operation="plan", provider="graph", stage="plan"):
                raise RuntimeError(f"boom {SECRET} {PROMPT}")

    span = exporter.get_finished_spans()[0]
    rendered = str(span.attributes)
    assert span.attributes is not None
    assert span.attributes["nexus.outcome"] == "error"
    assert SECRET not in rendered
    assert PROMPT not in rendered
    assert "boom" not in rendered


async def test_span_closes_on_cancellation(
    memory_tracer: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = memory_tracer

    async def blocked() -> None:
        with operation(operation="discover", provider="graph", stage="discover"):
            await asyncio.Event().wait()

    with bind_tracer(provider.get_tracer("nexus-test")):
        task = asyncio.create_task(blocked())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    span = exporter.get_finished_spans()[0]
    assert span.attributes is not None
    assert span.attributes["nexus.outcome"] == "cancelled"
    assert span.attributes["nexus.error_code"] == "interrupted"


async def test_mocked_run_correlates_spans_without_the_question(
    memory_tracer: tuple[TracerProvider, InMemorySpanExporter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, exporter = memory_tracer
    runner = ResearchRunner(
        graph=build_graph(InMemorySaver()),
        context=make_context(
            planner=FakePlanner(plan_for(["alpha limits"])),
            discovery=FakeDiscovery([empty_discovery()]),
            ingestion=FakeIngestion(),
            retrieval=FakeRetrieval([]),
            evaluator=ScriptedEvaluator([coverage(sufficient=False)]),
            synthesizer=StaticSynthesizer(),
        ),
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("log sink down")

    monkeypatch.setattr("nexus.observability.spans.log_event", fail)
    monkeypatch.setattr("nexus.observability.logging.log_event", fail)
    provider.add_span_processor(SimpleSpanProcessor(_BoomExporter()))  # type: ignore[arg-type]

    question = f"{QUESTION} {SECRET} {PROMPT}"
    with bind_tracer(provider.get_tracer("nexus-test")):
        result = await runner.run(question, uuid4())

    assert result.status == "insufficient_evidence"
    assert set(result.usage.model_dump()) == {
        "iterations",
        "search_calls",
        "llm_calls",
        "pages_indexed",
        "pages_failed",
    }
    spans = list(exporter.get_finished_spans())
    by_name = {span.name: span for span in spans}
    assert by_name["plan"].parent is not None
    assert by_name["plan"].parent.span_id == by_name["research_run"].context.span_id
    rendered = " ".join(str(span.attributes) for span in spans)
    assert SECRET not in rendered
    assert PROMPT not in rendered
    assert str(result.run_id) == by_name["research_run"].attributes["nexus.run_id"]


async def test_context_ids_are_visible_inside_a_child_task() -> None:
    seen: list[str] = []
    run_id = uuid4()

    async def child() -> None:
        from nexus.observability.context import current_context

        context = current_context()
        assert context.run_id is not None
        seen.append(str(context.run_id))

    with bind_run(run_id=run_id, research_id=uuid4(), stage="run"):
        await asyncio.create_task(child())

    assert seen == [str(run_id)]


async def test_bind_run_survives_yields_in_an_async_generator() -> None:
    from nexus.observability.context import current_context

    run_id = uuid4()
    seen: list[str | None] = []

    async def frames() -> AsyncIterator[str]:
        with bind_run(run_id=run_id, stage="run", create_buffer=True):
            yield "first"
            seen.append(
                None
                if current_context().run_id is None
                else str(current_context().run_id)
            )
            yield "second"

    collected = [item async for item in frames()]

    assert collected == ["first", "second"]
    assert seen == [str(run_id)]
    assert current_context().run_id is None
