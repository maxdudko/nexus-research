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


def _signed_metric(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except ArithmeticError, ValueError:
        raise ValueError("metric") from None
    quantized = parsed.quantize(Decimal("0.000001"))
    if parsed != quantized:
        raise ValueError("metric")
    return format(quantized, "f")


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

    @model_validator(mode="after")
    def consistent(self) -> Self:
        pair = (self.model is None) == (self.prompt_version is None)
        if self.outcome == "ok":
            valid = (
                self.structural_valid
                and self.predicted_label is not None
                and self.model is not None
                and self.prompt_version is not None
                and self.error_code is None
            )
        elif self.outcome == "skipped":
            valid = (
                not self.structural_valid
                and self.predicted_label is None
                and self.model is None
                and self.prompt_version is None
                and self.error_code is not None
            )
        else:
            valid = (
                self.structural_valid
                and self.predicted_label is None
                and self.error_code is not None
                and pair
            )
        if not valid:
            raise ValueError("judge outcome")
        return self


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


def case_matches(suite: SuiteName, case: StoredCase) -> bool:
    """True when the result object is the one allowed for that suite and category."""
    return type(case.result) is _RESULT_TYPES[(suite, case.category)]


class RetrievalDeltas(BaseModel):
    """Signed retrieval differences. A null metric stays null."""

    model_config = ConfigDict(extra="forbid")

    recall_at_k: str | None
    precision_at_k: str | None
    mrr_at_k: str | None
    defined_cases: int
    undefined_cases: int

    @field_validator("recall_at_k", "precision_at_k", "mrr_at_k")
    @classmethod
    def metrics(cls, value: str | None) -> str | None:
        return _signed_metric(value)


class CitationDeltas(BaseModel):
    """Signed citation-count differences."""

    model_config = ConfigDict(extra="forbid")

    cases: int
    valid: int
    invalid: int


class GroundingDeltas(BaseModel):
    """Signed grounding-count differences."""

    model_config = ConfigDict(extra="forbid")

    cases: int
    structurally_valid: int
    structurally_invalid: int


class JudgeCountDeltas(BaseModel):
    """Signed judge-count differences. These counts may be negative."""

    model_config = ConfigDict(extra="forbid")

    attempted: int
    succeeded: int
    failed: int
    skipped: int
    disagreed: int


class ComparisonDeltas(BaseModel):
    """Right minus left. Offline runs leave the judge fields empty."""

    model_config = ConfigDict(extra="forbid")

    retrieval: RetrievalDeltas
    citations: CitationDeltas
    grounding: GroundingDeltas
    agreement: str | None = None
    counts: JudgeCountDeltas | None = None
    matrix: dict[str, dict[str, int]] | None = None

    @field_validator("agreement")
    @classmethod
    def agreement_metric(cls, value: str | None) -> str | None:
        return _signed_metric(value)


class EvaluationListItem(BaseModel):
    """One history row. Cases and the confusion matrix are not included."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    suite: SuiteName
    outcome: OutcomeName
    imported_at: datetime
    executed_at: None = None
    dataset_version: str = Field(pattern=_DATASET_VERSION.pattern)
    dataset_sha256: str = Field(pattern=_SHA256.pattern)
    report_version: Literal["1"]
    scorer_version: Literal["1"]
    k: int = Field(ge=1, le=100)
    code_revision: str | None = Field(default=None, pattern=_REVISION.pattern)
    code_revision_source: RevisionSource | None = None
    provenance_level: ProvenanceName
    is_committed_baseline: bool
    model: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=64)
    temperature: str | None = None
    pricing_version: str | None = Field(default=None, min_length=1, max_length=64)
    retrieval: RetrievalSummary
    citations: CitationSummary
    grounding: GroundingSummary
    counts: JudgeRunCounts | None = None
    agreement: str | None = None
    compared: int | None = Field(default=None, ge=0)
    agreements: int | None = Field(default=None, ge=0)

    @field_validator("agreement")
    @classmethod
    def agreement_metric(cls, value: str | None) -> str | None:
        return _metric(value)

    @field_validator("temperature")
    @classmethod
    def temperature_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if re.fullmatch(r"\d+\.\d{3}", value) is None:
            raise ValueError("temperature")
        return value

    @field_validator("imported_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp")
        return value

    @model_validator(mode="after")
    def headlines(self) -> Self:
        live = self.suite == "live"
        live_fields = (
            self.model is not None
            and self.prompt_version is not None
            and self.temperature is not None
            and self.pricing_version is not None
            and self.counts is not None
            and self.compared is not None
            and self.agreements is not None
        )
        offline_empty = (
            self.model is None
            and self.prompt_version is None
            and self.temperature is None
            and self.pricing_version is None
            and self.counts is None
            and self.agreement is None
            and self.compared is None
            and self.agreements is None
        )
        if live != live_fields or (not live and not offline_empty):
            raise ValueError("headlines")
        if (self.code_revision is None) != (self.code_revision_source is None):
            raise ValueError("code revision")
        return self


class EvaluationDetail(EvaluationListItem):
    """Provenance, summary, config, and live calibration."""

    config: OfflineConfig | LiveConfig
    calibration: CalibrationReport | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        live = self.suite == "live"
        if live:
            if self.calibration is None or type(self.config) is not LiveConfig:
                raise ValueError("live detail")
            if (
                self.calibration.compared != self.compared
                or self.calibration.agreements != self.agreements
                or self.calibration.agreement != self.agreement
            ):
                raise ValueError("calibration")
        elif self.calibration is not None or type(self.config) is not OfflineConfig:
            raise ValueError("offline detail")
        return self


class EvaluationListResponse(BaseModel):
    """One keyset page of evaluation history."""

    model_config = ConfigDict(extra="forbid")

    items: list[EvaluationListItem]
    next_cursor: str | None


class EvaluationCasePage(BaseModel):
    """One keyset page of sanitized cases."""

    model_config = ConfigDict(extra="forbid")

    items: list[StoredCase]
    next_cursor: str | None


ReasonCode = Literal[
    "suite_mismatch",
    "dataset_mismatch",
    "report_version_mismatch",
    "scorer_mismatch",
    "k_mismatch",
    "judge_config_mismatch",
]
WarningCode = Literal["execution_time_unknown", "code_revision_unknown"]
PricingReason = Literal["pricing_unavailable", "pricing_version_mismatch"]


class ComparisonResponse(BaseModel):
    """Compatibility, stored headlines, and deltas only when the pair matches."""

    model_config = ConfigDict(extra="forbid")

    comparable: bool
    reasons: list[ReasonCode]
    warnings: list[WarningCode]
    cost_comparable: bool
    pricing_reason: PricingReason | None
    left: EvaluationDetail
    right: EvaluationDetail
    deltas: ComparisonDeltas | None

    @model_validator(mode="after")
    def contract(self) -> Self:
        if self.comparable == (self.deltas is None):
            raise ValueError("deltas")
        if self.comparable == bool(self.reasons):
            raise ValueError("reasons")
        if self.cost_comparable == (self.pricing_reason is not None):
            raise ValueError("pricing")
        return self
