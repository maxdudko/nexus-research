"""Allowlisted evaluation rows. Free text is not a field."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nexus.evaluation.calibration import LABELS
from nexus.evaluation.schemas import (
    SCORER_VERSION,
    CalibrationReport,
    JudgeOutcome,
    JudgeRunCounts,
    SupportLabel,
)

_METRIC = re.compile(r"^\d+\.\d{6}$")
_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_DATASET_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SuiteName = Literal["offline", "live"]
OutcomeName = Literal["passed", "failed", "informational"]
ProvenanceName = Literal["offline_recomputed", "operator_imported"]
CategoryName = Literal["retrieval", "citation", "grounding"]
RevisionSource = Literal["operator_asserted"]


def _metric(value: str | None) -> str | None:
    if value is None:
        return None
    if _METRIC.fullmatch(value) is None:
        raise ValueError("metric")
    return value


def _code(value: str | None) -> str | None:
    if value is None:
        return None
    if _CODE.fullmatch(value) is None:
        raise ValueError("error code")
    return value


class RetrievalResult(BaseModel):
    """Retrieval metrics for one case. Null means the gold set was empty."""

    model_config = ConfigDict(extra="forbid")

    gold_count: int = Field(ge=0)
    recall_at_k: str | None
    precision_at_k: str | None
    mrr_at_k: str | None

    @field_validator("recall_at_k", "precision_at_k", "mrr_at_k")
    @classmethod
    def metrics(cls, value: str | None) -> str | None:
        return _metric(value)


class CitationResult(BaseModel):
    """Citation provenance for one case. The claim text is not stored."""

    model_config = ConfigDict(extra="forbid")

    citation_valid: bool
    expect_citation_valid: bool
    support_label: SupportLabel
    error_codes: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("error_codes")
    @classmethod
    def codes(cls, value: list[str]) -> list[str]:
        for item in value:
            _code(item)
        return value


class GroundingStructureResultPayload(BaseModel):
    """Structural grounding for one offline case."""

    model_config = ConfigDict(extra="forbid")

    structural_valid: bool
    expect_structurally_valid: bool
    gold_label: SupportLabel
    error_code: str | None = None
    excerpt_count: int = Field(ge=0)

    @field_validator("error_code")
    @classmethod
    def code(cls, value: str | None) -> str | None:
        return _code(value)


class GroundingJudgeResultPayload(GroundingStructureResultPayload):
    """A live judge case without rationale, excerpts, or token counts."""

    predicted_label: SupportLabel | None
    outcome: JudgeOutcome
    model: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=64)


class RetrievalSummary(BaseModel):
    """Macro-average stored beside the run."""

    model_config = ConfigDict(extra="forbid")

    recall_at_k: str | None
    precision_at_k: str | None
    mrr_at_k: str | None
    defined_cases: int = Field(ge=0)
    undefined_cases: int = Field(ge=0)

    @field_validator("recall_at_k", "precision_at_k", "mrr_at_k")
    @classmethod
    def metrics(cls, value: str | None) -> str | None:
        return _metric(value)


class CitationSummary(BaseModel):
    """How many citation cases matched their expected validity."""

    model_config = ConfigDict(extra="forbid")

    cases: int = Field(ge=0)
    valid: int = Field(ge=0)
    invalid: int = Field(ge=0)

    @model_validator(mode="after")
    def totals(self) -> Self:
        if self.valid + self.invalid != self.cases:
            raise ValueError("citation counts")
        return self


class GroundingSummary(BaseModel):
    """How many grounding cases were structurally valid."""

    model_config = ConfigDict(extra="forbid")

    cases: int = Field(ge=0)
    structurally_valid: int = Field(ge=0)
    structurally_invalid: int = Field(ge=0)

    @model_validator(mode="after")
    def totals(self) -> Self:
        if self.structurally_valid + self.structurally_invalid != self.cases:
            raise ValueError("grounding counts")
        return self


class OfflineSummary(BaseModel):
    """Counts and aggregate metrics. The raw artifact is not included."""

    model_config = ConfigDict(extra="forbid")

    retrieval: RetrievalSummary
    citations: CitationSummary
    grounding: GroundingSummary


class LiveSummary(OfflineSummary):
    """Offline summary plus recomputed judge counts and the confusion matrix."""

    counts: JudgeRunCounts
    calibration: CalibrationReport

    @model_validator(mode="after")
    def labels(self) -> Self:
        if tuple(self.calibration.labels) != LABELS:
            raise ValueError("label taxonomy")
        return self


class OfflineConfig(BaseModel):
    """Metric-contract markers stored with every run."""

    model_config = ConfigDict(extra="forbid")

    empty_gold: Literal["undefined"]
    metric_quant: Literal["0.000001"]


class LiveConfig(OfflineConfig):
    """Live runs also record the judge label order."""

    label_taxonomy: list[SupportLabel] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def taxonomy(self) -> Self:
        if tuple(self.label_taxonomy) != LABELS:
            raise ValueError("label taxonomy")
        return self


class StoredCase(BaseModel):
    """One case. ``result`` is chosen from the category's allowlist."""

    model_config = ConfigDict(extra="forbid")

    category: CategoryName
    case_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    result: (
        RetrievalResult
        | CitationResult
        | GroundingJudgeResultPayload
        | GroundingStructureResultPayload
    )


_RESULT_TYPES: dict[tuple[SuiteName, CategoryName], type[BaseModel]] = {
    ("offline", "retrieval"): RetrievalResult,
    ("offline", "citation"): CitationResult,
    ("offline", "grounding"): GroundingStructureResultPayload,
    ("live", "retrieval"): RetrievalResult,
    ("live", "citation"): CitationResult,
    ("live", "grounding"): GroundingJudgeResultPayload,
}


class EvaluationRunInsert(BaseModel):
    """The only object the repository inserts."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    suite: SuiteName
    outcome: OutcomeName
    artifact_sha256: str = Field(pattern=_SHA256.pattern)
    dataset_version: str = Field(pattern=_DATASET_VERSION.pattern)
    dataset_sha256: str = Field(pattern=_SHA256.pattern)
    report_version: Literal["1"]
    scorer_version: Literal["1"]
    k: int = Field(ge=1, le=100)
    code_revision: str | None = Field(default=None, pattern=_REVISION.pattern)
    code_revision_source: RevisionSource | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=64)
    temperature: Decimal | None = None
    pricing_version: str | None = Field(default=None, min_length=1, max_length=64)
    executed_at: None = None
    imported_at: datetime
    provenance_level: ProvenanceName
    is_committed_baseline: bool
    summary: OfflineSummary | LiveSummary
    config: OfflineConfig | LiveConfig
    cases: list[StoredCase]

    @field_validator("scorer_version")
    @classmethod
    def scorer(cls, value: str) -> str:
        if value != SCORER_VERSION:
            raise ValueError("scorer version")
        return value

    @field_validator("temperature", mode="before")
    @classmethod
    def quantize_temperature(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(
            value, int | float | str | Decimal
        ):
            raise ValueError("temperature")
        parsed = Decimal(str(value)).quantize(Decimal("0.001"))
        if parsed < 0 or parsed > 2:
            raise ValueError("temperature")
        return parsed

    @field_validator("imported_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp")
        return value

    @model_validator(mode="after")
    def contract(self) -> Self:
        live = self.suite == "live"
        fields_present = (
            self.model is not None
            and self.prompt_version is not None
            and self.temperature is not None
            and self.pricing_version is not None
        )
        if fields_present != live:
            raise ValueError("live fields")
        if live and self.outcome != "informational":
            raise ValueError("outcome")
        if not live and self.outcome == "informational":
            raise ValueError("outcome")
        if live != (self.provenance_level == "operator_imported"):
            raise ValueError("provenance")
        if (self.code_revision is None) != (self.code_revision_source is None):
            raise ValueError("code revision")
        if self.is_committed_baseline and not (
            self.suite == "offline"
            and self.outcome == "passed"
            and self.provenance_level == "offline_recomputed"
        ):
            raise ValueError("baseline")
        summary_type = LiveSummary if live else OfflineSummary
        config_type = LiveConfig if live else OfflineConfig
        if (
            type(self.summary) is not summary_type
            or type(self.config) is not config_type
        ):
            raise ValueError("summary")
        seen: set[tuple[str, str]] = set()
        for case in self.cases:
            key = (case.category, case.case_id)
            if key in seen:
                raise ValueError("duplicate case")
            seen.add(key)
            if type(case.result) is not _RESULT_TYPES[(self.suite, case.category)]:
                raise ValueError("case result")
        return self
