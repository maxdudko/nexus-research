"""Bounded evidence analysis. All ids are checked against the current snapshot."""

from pydantic import BaseModel, ConfigDict, Field


class AnalyzedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    statement: str = Field(min_length=1, max_length=2000)
    supporting_evidence: list[str] = Field(max_length=20)
    contradicting_evidence: list[str] = Field(max_length=20)
    limitations: list[str] = Field(max_length=5)


class EvidenceAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[AnalyzedClaim] = Field(max_length=60)
    covered_task_ids: list[str] = Field(max_length=3)
    missing_task_ids: list[str] = Field(max_length=3)
    gaps: list[str] = Field(max_length=3)
    next_queries: list[str] = Field(max_length=3)


class ClaimCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    verdict: str
    reason: str = Field(max_length=1000)


class ReportVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[ClaimCheck] = Field(max_length=200)
    missing_task_ids: list[str] = Field(default_factory=list, max_length=3)


class SourceRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordered_indices: list[int] = Field(max_length=15)
