"""Sequential research graph. Services travel in runtime context, not state."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from nexus.ingestion.service import IngestionResult
from nexus.observability.context import bind_run
from nexus.observability.spans import operation
from nexus.research.budgets import BudgetLimits
from nexus.research.nodes.discovery import discover_node
from nexus.research.nodes.evaluation import evaluate_node
from nexus.research.nodes.failure import fail_node
from nexus.research.nodes.ingestion import ingest_node
from nexus.research.nodes.planning import plan_node
from nexus.research.nodes.retrieval import retrieve_node
from nexus.research.nodes.synthesis import synthesize_node
from nexus.research.outcomes import EvaluationOutcome, SynthesisOutcome
from nexus.research.routing import RouteName, decide, route_after_plan
from nexus.research.state import ResearchState
from nexus.retrieval.vector_store import EvidenceMatch
from nexus.schemas.report import ReportStatus
from nexus.schemas.research import DiscoveryResult, ResearchPlan

RECURSION_LIMIT = 25


class PlannerProtocol(Protocol):
    """The planner method the graph calls."""

    async def create_plan(self, question: str) -> ResearchPlan: ...


class DiscoveryProtocol(Protocol):
    """The discovery method the graph calls. Queries stay inside the question."""

    async def discover(self, question: str) -> DiscoveryResult: ...


class WebIngestor(Protocol):
    """Index one public page in an existing research scope."""

    async def ingest_web(self, research_id: UUID, url: str) -> IngestionResult: ...


class EvidenceSearcher(Protocol):
    """Search accepted passages for one research scope."""

    async def search(
        self,
        research_id: UUID,
        query: str,
        top_k: int,
    ) -> list[EvidenceMatch]: ...


class CoverageEvaluator(Protocol):
    """Coverage advice. It cannot raise the budgets."""

    async def evaluate(self, state: ResearchState) -> EvaluationOutcome: ...


class ReportSynthesizer(Protocol):
    """Build a report for a status the router already chose."""

    async def synthesize(
        self,
        state: ResearchState,
        *,
        status: ReportStatus,
        max_calls: int,
    ) -> SynthesisOutcome: ...


@dataclass
class GraphContext:
    """Services for one run. LangGraph does not checkpoint this object."""

    planner: PlannerProtocol
    discovery: DiscoveryProtocol
    ingestion: WebIngestor
    retrieval: EvidenceSearcher
    evaluator: CoverageEvaluator
    synthesizer: ReportSynthesizer
    limits: BudgetLimits
    clock: Callable[[], datetime]


def build_graph(
    checkpointer: BaseCheckpointSaver | None = None,  # type: ignore[type-arg]
) -> CompiledStateGraph[ResearchState, GraphContext, ResearchState, ResearchState]:
    """Compile the bounded workflow. Recursion limit is only a backstop."""
    graph = StateGraph(ResearchState, context_schema=GraphContext)
    graph.add_node("plan", _observe("plan", plan_node))
    graph.add_node("discover", _observe("discover", discover_node))
    graph.add_node("ingest", _observe("ingest", ingest_node))
    graph.add_node("retrieve", _observe("retrieve", retrieve_node))
    graph.add_node("evaluate", _observe("evaluate", evaluate_node))
    graph.add_node("synthesize", _observe("synthesize", synthesize_node))
    graph.add_node("fail", _observe("fail", fail_node))
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {"discover": "discover", "fail": "fail"},
    )
    graph.add_edge("discover", "ingest")
    graph.add_edge("ingest", "retrieve")
    graph.add_edge("retrieve", "evaluate")
    graph.add_conditional_edges(
        "evaluate",
        route_after_evaluation,
        {
            "continue_discovery": "discover",
            "synthesize": "synthesize",
            "insufficient_evidence": "synthesize",
            "fail": "fail",
        },
    )
    graph.add_edge("synthesize", END)
    graph.add_edge("fail", END)
    return graph.compile(checkpointer=checkpointer)


def _observe(stage: str, node: Any) -> Any:
    """Time one node. The edges and checkpointed state stay unchanged."""

    async def wrapped(
        state: ResearchState,
        runtime: Runtime[GraphContext],
    ) -> dict[str, object]:
        with bind_run(stage=stage, iteration=state["iteration"]):
            with operation(operation=stage, provider="graph", stage=stage) as handle:
                result = await node(state, runtime)
                updated = result.get("iteration")
                if type(updated) is int and updated >= 0:
                    handle.iteration = updated
                code = result.get("error_code")
                if isinstance(code, str) and code:
                    handle.outcome = "error"
                    handle.error_code = code
                return cast(dict[str, object], result)

    return wrapped


def route_after_evaluation(
    state: ResearchState,
    runtime: Runtime[GraphContext],
) -> RouteName:
    """Policy edge. The evaluator's flag is only one of the inputs."""
    return decide(state, runtime.context.limits, runtime.context.clock())


def run_config(run_id: str) -> RunnableConfig:
    """Thread id is the run id. Services are not stored in configurable."""
    return {
        "recursion_limit": RECURSION_LIMIT,
        "configurable": {"thread_id": run_id},
    }
