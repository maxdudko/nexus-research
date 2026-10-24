"""Versioned evaluation contracts. These models do not call a provider."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATASET_VERSION = "2026-10-19.2"
REPORT_VERSION = "1"
SCORER_VERSION: Literal["1"] = "1"
K = 5
EXCERPT_CHARS = 500
PROMPT_VERSION = "grounding-judge-2"
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_RETRIES = 0

SupportLabel = Literal[
    "supported",
    "unsupported",
    "insufficient_context",
    "contradictory",
]
ChunkKind = Literal["web", "pdf"]
JudgeOutcome = Literal["skipped", "ok", "error"]
UsageSourceName = Literal["provider", "estimated", "unavailable"]


class CorpusChunk(BaseModel):
    """One authored passage. The semantic id is the gold key."""

    model_config = ConfigDict(extra="forbid")

    semantic_id: str = Field(pattern=r"^[a-z0-9-]+#c[0-9]+$")
    research_id: UUID
    source_id: UUID
    chunk_index: int = Field(ge=0)
    kind: ChunkKind
    url: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def provenance(self) -> CorpusChunk:
        if self.kind == "web":
            if self.url is None or self.page_number is not None:
                raise ValueError("web chunks have a url and no page")
        elif self.page_number is None or self.url is not None:
            raise ValueError("pdf chunks have a page and no url")
        return self


class QueryCase(BaseModel):
    """A question and the semantic ids that count as relevant."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    question: str = Field(min_length=1, max_length=300)
    gold: list[str] = Field(default_factory=list)


class RankCase(BaseModel):
    """A frozen ordering. Later duplicates are ignored by the scorer."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    ranked: list[str] = Field(default_factory=list)


class CitationSource(BaseModel):
    """One label pointing at a semantic passage."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=16)
    semantic_id: str = Field(min_length=1, max_length=64)


class CitationCase(BaseModel):
    """Provenance for one claim. The support label is not a validity check."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    research_id: UUID
    support_label: SupportLabel
    claim: str = Field(min_length=1, max_length=500)
    citations: list[str] = Field(default_factory=list)
    sources: list[CitationSource] = Field(default_factory=list)
    expect_citation_valid: bool


class GroundingCase(BaseModel):
    """A labeled claim. Entailment is not decided by the validator."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    research_id: UUID
    claim: str = Field(min_length=1, max_length=500)
    cited_semantic_ids: list[str] = Field(min_length=1)
    gold_label: SupportLabel
    expect_structurally_valid: bool


class PreparedExcerpt(BaseModel):
    """A cited passage after the character cap."""

    model_config = ConfigDict(extra="forbid")

    semantic_id: str
    text: str = Field(max_length=EXCERPT_CHARS)


class PreparedClaim(BaseModel):
    """The only text a judge is allowed to see for one case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    claim: str
    excerpts: list[PreparedExcerpt]


class JudgeVerdict(BaseModel):
    """Structured judge output. Rationale is not a log field."""

    model_config = ConfigDict(extra="forbid")

    verdict: SupportLabel
    rationale: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class RetrievalCaseResult(BaseModel):
    """Metrics for one query. Null means the gold set was empty."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    gold_count: int = Field(ge=0)
    recall_at_k: str | None
    precision_at_k: str | None
    mrr_at_k: str | None


class RetrievalAggregate(BaseModel):
    """Macro-average over cases with a non-empty gold set."""

    model_config = ConfigDict(extra="forbid")

    recall_at_k: str | None
    precision_at_k: str | None
    mrr_at_k: str | None
    defined_cases: int = Field(ge=0)
    undefined_cases: int = Field(ge=0)


class RetrievalReport(BaseModel):
    """Frozen-list scores. They are not a pass/fail threshold."""

    model_config = ConfigDict(extra="forbid")

    k: int
    cases: list[RetrievalCaseResult]
    aggregate: RetrievalAggregate


class CitationCheck(BaseModel):
    """Whether the labels resolve. Support is recorded beside that result."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    citation_valid: bool
    support_label: SupportLabel
    expect_citation_valid: bool
    error_codes: list[str] = Field(default_factory=list)


class CitationReport(BaseModel):
    """Provenance checks for the fixture claims."""

    model_config = ConfigDict(extra="forbid")

    cases: list[CitationCheck]


class GroundingStructureResult(BaseModel):
    """Validator output. It does not say whether the claim is entailed."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    gold_label: SupportLabel
    structural_valid: bool
    expect_structurally_valid: bool
    error_code: str | None = None
    excerpt_count: int = Field(default=0, ge=0)


class GroundingJudgeResult(BaseModel):
    """One judge observation. Missing token counts stay null."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    gold_label: SupportLabel
    structural_valid: bool
    predicted_label: SupportLabel | None = None
    outcome: JudgeOutcome
    model: str | None = None
    prompt_version: str | None = None
    dataset_version: str
    temperature: float | None = None
    timestamp: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    usage_source: UsageSourceName | None = None
    error_code: str | None = None
    rationale: str | None = None


class JudgeRunCounts(BaseModel):
    """How many judge calls were sent, and how those calls finished."""

    model_config = ConfigDict(extra="forbid")

    attempted: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    disagreed: int = Field(ge=0)


class CalibrationReport(BaseModel):
    """Agreement on the authored set. Both figures are estimates."""

    model_config = ConfigDict(extra="forbid")

    estimate: Literal[True] = True
    labels: list[str]
    matrix: dict[str, dict[str, int]]
    compared: int = Field(ge=0)
    agreements: int = Field(ge=0)
    agreement: str | None = None


class OfflineReport(BaseModel):
    """The artifact written by the offline suite."""

    model_config = ConfigDict(extra="forbid")

    report_version: Literal["1"] = "1"
    dataset_version: str
    k: int
    suite: Literal["offline"] = "offline"
    retrieval: RetrievalReport
    citations: CitationReport
    grounding: list[GroundingStructureResult]


class LiveReport(BaseModel):
    """The artifact written after an opted-in judge run."""

    model_config = ConfigDict(extra="forbid")

    report_version: Literal["1"] = "1"
    dataset_version: str
    k: int
    suite: Literal["live"] = "live"
    model: str
    prompt_version: str
    temperature: float
    pricing_version: str
    retrieval: RetrievalReport
    citations: CitationReport
    grounding: list[GroundingJudgeResult]
    counts: JudgeRunCounts
    calibration: CalibrationReport
