"""Request-scoped research runs. A checkpoint is not a worker."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.ai.coverage import OpenAICoverageEvaluator
from nexus.ai.errors import (
    MODEL_REFUSED,
    ResearchError,
    checkpoint_unavailable,
    deadline_exceeded,
    invalid_model_output,
    invalid_state,
    provider_not_configured,
    provider_rate_limited,
    provider_timeout,
    provider_unavailable,
    run_interrupted,
    search_failed,
)
from nexus.ai.planner import Planner
from nexus.ai.synthesis import OpenAIReportSynthesizer
from nexus.core.config import Settings
from nexus.ingestion.service import IngestionService
from nexus.research.budgets import limits_from_settings
from nexus.research.discovery import Discovery
from nexus.research.graph import GraphContext, run_config
from nexus.research.state import (
    GraphStateError,
    ResearchState,
    graph_usage,
    initial_state,
    validate_state,
)
from nexus.retrieval.embeddings import OpenAIEmbedder
from nexus.retrieval.service import RetrievalService
from nexus.retrieval.vector_store import EvidenceVectorStore
from nexus.schemas.report import ResearchReport
from nexus.schemas.research import ResearchRunResponse

logger = logging.getLogger(__name__)

DEADLINE_GRACE_SECONDS = 5
_REPORT_STATUSES = {"completed", "partial", "insufficient_evidence"}


GraphRun = CompiledStateGraph[ResearchState, GraphContext, ResearchState, ResearchState]


class ResearchRunner:
    """Run one new thread to a terminal state. The same thread is never reused."""

    def __init__(
        self,
        *,
        graph: GraphRun,
        context: GraphContext,
        timeout_seconds: float | None = None,
    ) -> None:
        self.graph = graph
        self._context = context
        self._timeout = timeout_seconds

    async def run(
        self,
        question: str,
        research_id: UUID | None,
    ) -> ResearchRunResponse:
        """Execute one question. A new run id is the LangGraph thread id."""
        scope = research_id or uuid4()
        run_id = uuid4()
        state = initial_state(
            research_id=scope,
            run_id=run_id,
            question=question,
            started_at=self._context.clock(),
        )
        try:
            async with asyncio.timeout(self._budget()):
                result = await self.graph.ainvoke(
                    state,
                    config=run_config(str(run_id)),
                    context=self._context,
                )
        except TimeoutError:
            await self._interrupt(str(run_id))
            raise deadline_exceeded() from None
        except asyncio.CancelledError:
            raise
        except ResearchError:
            raise
        except Exception as exc:
            if _checkpoint_failure(exc):
                logger.warning("checkpoint write failed: %s", type(exc).__name__)
                raise checkpoint_unavailable() from None
            raise
        return _response(_checked(result))

    def _budget(self) -> float:
        if self._timeout is not None:
            return self._timeout
        return self._context.limits.deadline_seconds + DEADLINE_GRACE_SECONDS

    async def _interrupt(self, run_id: str) -> None:
        """Record the hard stop on the checkpoint. This does not continue the run."""
        try:
            await self.graph.aupdate_state(
                run_config(run_id),
                {
                    "status": "interrupted",
                    "report": None,
                    "error_code": "interrupted",
                },
            )
        except Exception as exc:
            logger.warning(
                "could not record an interrupted run: %s", type(exc).__name__
            )


def production_context(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    store: EvidenceVectorStore,
) -> GraphContext:
    """The services one HTTP run calls. The store is closed by the caller."""
    embedder = OpenAIEmbedder(settings)
    return GraphContext(
        planner=Planner(settings),
        discovery=Discovery(settings),
        ingestion=IngestionService(sessions, store, embedder, settings),
        retrieval=RetrievalService(sessions, store, embedder),
        evaluator=OpenAICoverageEvaluator(settings),
        synthesizer=OpenAIReportSynthesizer(settings),
        limits=limits_from_settings(settings),
        clock=lambda: datetime.now(UTC),
    )


def _checked(result: object) -> ResearchState:
    try:
        return validate_state(result)
    except GraphStateError:
        raise invalid_state() from None


def _response(state: ResearchState) -> ResearchRunResponse:
    status = state["status"]
    report = state["report"]
    if status in _REPORT_STATUSES and report is not None:
        parsed = ResearchReport.model_validate(report)
        if parsed.status != status:
            raise invalid_state()
        return ResearchRunResponse(
            research_id=UUID(state["research_id"]),
            run_id=UUID(state["run_id"]),
            status=parsed.status,
            report=parsed,
            warnings=list(state["warnings"]),
            usage=graph_usage(state),
        )
    if status == "interrupted":
        raise run_interrupted()
    if status == "running":
        raise invalid_state()
    raise _failure(state["error_code"])


def _failure(code: str | None) -> ResearchError:
    if code == "model_refused":
        return ResearchError("model_refused", 503, MODEL_REFUSED)
    builders = {
        "provider_not_configured": provider_not_configured,
        "provider_timeout": provider_timeout,
        "provider_rate_limited": provider_rate_limited,
        "provider_unavailable": provider_unavailable,
        "invalid_model_output": invalid_model_output,
        "invalid_state": invalid_state,
        "search_failed": search_failed,
        "checkpoint_unavailable": checkpoint_unavailable,
        "deadline_exceeded": deadline_exceeded,
        "interrupted": run_interrupted,
    }
    builder = builders.get(code or "")
    if builder is None:
        return provider_unavailable()
    return builder()


def _checkpoint_failure(exc: BaseException) -> bool:
    return type(exc).__module__.startswith("psycopg")
