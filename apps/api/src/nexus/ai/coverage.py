"""Coverage evaluation. The model advises; it does not raise the budgets."""

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from nexus.ai.client import StructuredOutputModel
from nexus.ai.errors import ResearchError, invalid_model_output, map_provider_exception
from nexus.ai.planner import StructuredModel
from nexus.ai.prompts import EVALUATION_SYSTEM_PROMPT, evaluation_user_prompt
from nexus.core.config import Settings
from nexus.research.outcomes import EvaluationOutcome
from nexus.research.routing import assign_evidence_ids
from nexus.research.state import CoverageState, ResearchState
from nexus.schemas.coverage import (
    CoverageEvaluation,
    task_label,
    task_labels,
    validate_coverage,
)
from nexus.schemas.research import ResearchPlan


class OpenAICoverageEvaluator:
    """One structured coverage call. A retry would spend the other visit."""

    def __init__(
        self,
        settings: Settings,
        model: StructuredModel | None = None,
    ) -> None:
        self._settings = settings
        self._model = model

    async def evaluate(self, state: ResearchState) -> EvaluationOutcome:
        plan = _plan(state)
        ids = task_labels(len(plan.tasks))
        passages = _passages(state)
        messages: list[BaseMessage] = [
            SystemMessage(content=EVALUATION_SYSTEM_PROMPT),
            HumanMessage(
                content=evaluation_user_prompt(
                    question=state["question"],
                    tasks=[
                        (task_label(index), task.question)
                        for index, task in enumerate(plan.tasks, start=1)
                    ],
                    passages=passages,
                )
            ),
        ]
        model = self._model or StructuredOutputModel(
            self._settings,
            CoverageEvaluation,
        )
        try:
            result = await model.ainvoke(messages)
            checked = validate_coverage(
                result,
                task_ids=ids,
                has_evidence=bool(state["evidence"]),
            )
        except ResearchError:
            raise
        except Exception as exc:
            raise map_provider_exception(exc) from None
        return EvaluationOutcome(coverage=_coverage_state(checked), llm_calls=1)


def _plan(state: ResearchState) -> ResearchPlan:
    plan = state["plan"]
    if plan is None:
        raise invalid_model_output()
    try:
        return ResearchPlan.model_validate(plan)
    except ValidationError:
        raise invalid_model_output() from None


def _passages(state: ResearchState) -> list[tuple[str, str]]:
    by_chunk = {item["chunk_id"]: item["excerpt"] for item in state["evidence"]}
    return [
        (ref.evidence_id, by_chunk.get(ref.chunk_id, ""))
        for ref in assign_evidence_ids(state["evidence"])
    ]


def _coverage_state(value: CoverageEvaluation) -> CoverageState:
    return {
        "sufficient": value.sufficient,
        "covered_task_ids": list(value.covered_task_ids),
        "missing_task_ids": list(value.missing_task_ids),
        "gaps": list(value.gaps),
        "uncertainty": value.uncertainty,
    }
