"""Fakes for the offline research graph. No provider clients."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from langgraph.runtime import Runtime
from tests.conftest import make_settings

from nexus.ingestion.service import IngestionResult
from nexus.research.budgets import limits_from_settings
from nexus.research.graph import GraphContext, ReportSynthesizer
from nexus.research.nodes.evaluation import StaticEvaluator
from nexus.research.nodes.synthesis import StaticSynthesizer
from nexus.research.outcomes import EvaluationOutcome
from nexus.research.state import (
    CoverageState,
    ResearchState,
    initial_state,
    merge_update,
)
from nexus.retrieval.vector_store import EvidenceMatch
from nexus.schemas.research import (
    DiscoveryResult,
    ResearchPlan,
    ResearchTask,
    SearchResult,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
QUESTION = "How do bounded research graphs stop?"


class FakePlanner:
    """Returns one scripted plan, or raises the scripted error."""

    def __init__(self, result: ResearchPlan | Exception) -> None:
        self._result = result
        self.questions: list[str] = []

    async def create_plan(self, question: str) -> ResearchPlan:
        self.questions.append(question)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeDiscovery:
    """Returns scripted discovery results in order."""

    def __init__(self, results: list[DiscoveryResult | Exception]) -> None:
        self._results = list(results)
        self.questions: list[str] = []

    async def discover(self, question: str) -> DiscoveryResult:
        self.questions.append(question)
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeIngestion:
    """Indexes scripted pages. An empty script succeeds with a new source id."""

    def __init__(
        self, results: list[IngestionResult | Exception] | None = None
    ) -> None:
        self._results = list(results or [])
        self.calls: list[tuple[UUID, str]] = []

    async def ingest_web(self, research_id: UUID, url: str) -> IngestionResult:
        self.calls.append((research_id, url))
        if self._results:
            item = self._results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return IngestionResult(
            source_id=uuid4(),
            research_id=research_id,
            status="indexed",
            chunk_count=1,
            deduplicated=False,
        )


class FakeRetrieval:
    """Returns the same matches for every query, or raises one error.

    ``adopt_scope`` rewrites each match into the research id being searched.
    The retrieval node drops any other scope.
    """

    def __init__(
        self,
        matches: list[EvidenceMatch] | Exception,
        *,
        adopt_scope: bool = False,
    ) -> None:
        self._matches = matches
        self._adopt_scope = adopt_scope
        self.calls: list[tuple[UUID, str, int]] = []

    async def search(
        self,
        research_id: UUID,
        query: str,
        top_k: int,
    ) -> list[EvidenceMatch]:
        self.calls.append((research_id, query, top_k))
        if isinstance(self._matches, Exception):
            raise self._matches
        if self._adopt_scope:
            return [replace(match, research_id=research_id) for match in self._matches]
        return list(self._matches)


class ScriptedEvaluator:
    """Returns scripted coverage advice. It does not change budgets."""

    def __init__(self, replies: list[CoverageState]) -> None:
        self._replies = list(replies)

    async def evaluate(self, state: ResearchState) -> EvaluationOutcome:
        del state
        return EvaluationOutcome(coverage=self._replies.pop(0), llm_calls=0)


def plan_for(queries: list[str]) -> ResearchPlan:
    """Build a valid plan. Each task holds at most three queries."""
    tasks: list[ResearchTask] = []
    for offset in range(0, len(queries), 3):
        chunk = queries[offset : offset + 3]
        tasks.append(
            ResearchTask(
                question=f"What does group {offset} cover?",
                search_queries=chunk,
            )
        )
    return ResearchPlan(objective="Find the practical limits.", tasks=tasks)


def discovered(
    url: str,
    *,
    search_calls: int,
    llm_calls: int,
    snippet: str = "A short untrusted snippet.",
) -> DiscoveryResult:
    """One discovery result. The snippet must not be copied into state."""
    return DiscoveryResult(
        status="completed",
        summary="Retrieved sources.",
        sources=[SearchResult(title="Notes", url=url, snippet=snippet)],
        search_calls=search_calls,
        llm_calls=llm_calls,
        warnings=[],
    )


def empty_discovery(*, search_calls: int = 1, llm_calls: int = 1) -> DiscoveryResult:
    """Discovery finished without any URLs."""
    return DiscoveryResult(
        status="partial",
        summary="No sources were retrieved.",
        sources=[],
        search_calls=search_calls,
        llm_calls=llm_calls,
        warnings=["insufficient_sources"],
    )


def coverage(*, sufficient: bool) -> CoverageState:
    """Minimal coverage advice for the router."""
    return {
        "sufficient": sufficient,
        "covered_task_ids": [],
        "missing_task_ids": [],
        "gaps": [],
        "uncertainty": "",
    }


def make_match(
    research_id: UUID,
    *,
    text: str = "A short excerpt.",
    score: float = 0.8,
    url: str | None = "https://example.org/notes",
    chunk_id: UUID | None = None,
    page_number: int | None = None,
) -> EvidenceMatch:
    """One retrieved passage. The text is untrusted data."""
    return EvidenceMatch(
        chunk_id=chunk_id or uuid4(),
        source_id=uuid4(),
        research_id=research_id,
        score=score,
        text=text,
        page_number=page_number,
        url=url,
        title="Notes",
        content_hash="abc123",
        chunk_index=0,
    )


def make_state() -> ResearchState:
    """A running state whose clock matches the graph context."""
    return initial_state(
        research_id=uuid4(),
        run_id=uuid4(),
        question=QUESTION,
        started_at=NOW,
    )


def with_plan(state: ResearchState, queries: list[str]) -> ResearchState:
    """Attach a plan without charging discovery."""
    return merge_update(state, {"plan": plan_for(queries).model_dump(mode="json")})


def make_context(
    *,
    planner: FakePlanner,
    discovery: FakeDiscovery,
    ingestion: FakeIngestion,
    retrieval: FakeRetrieval,
    evaluator: ScriptedEvaluator | StaticEvaluator | None = None,
    synthesizer: ReportSynthesizer | None = None,
) -> GraphContext:
    """Services for one offline run. The clock does not advance."""
    return GraphContext(
        planner=planner,
        discovery=discovery,
        ingestion=ingestion,
        retrieval=retrieval,
        evaluator=evaluator or StaticEvaluator(),
        synthesizer=synthesizer or StaticSynthesizer(),
        limits=limits_from_settings(make_settings()),
        clock=lambda: NOW,
    )


def runtime_for(context: GraphContext) -> Runtime[GraphContext]:
    """The same runtime object LangGraph passes into a node."""
    return Runtime(context=context)
