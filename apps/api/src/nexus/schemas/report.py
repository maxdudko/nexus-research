"""Checkpoint-safe report contracts. Citation text is not factual entailment."""

import re
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReportStatus = Literal["completed", "partial", "insufficient_evidence"]
_EVIDENCE_ID = re.compile(r"^E[1-9][0-9]*$")


class GraphUsage(BaseModel):
    """Provider and loop counts for one run. SDK retries are not included."""

    model_config = ConfigDict(extra="forbid")

    iterations: int = Field(ge=0)
    search_calls: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    pages_indexed: int = Field(ge=0)
    pages_failed: int = Field(ge=0)


class EvidenceRef(BaseModel):
    """One server-assigned citation target. The id is not chosen by the model."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_id: str
    chunk_id: str
    research_id: str
    url: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    title: str | None = None
    domain: str | None = None
    published_at: str | None = None
    retrieved_at: str | None = None
    query: str | None = None
    excerpt: str | None = Field(default=None, max_length=2000)

    @field_validator("evidence_id")
    @classmethod
    def evidence_label(cls, value: str) -> str:
        if _EVIDENCE_ID.fullmatch(value) is None:
            raise ValueError("must be an evidence id")
        return value

    @field_validator("source_id", "chunk_id", "research_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        return _canonical_uuid(value)


class ModelSection(BaseModel):
    """One section as the model may write it. Provenance is not a field."""

    model_config = ConfigDict(extra="forbid")

    heading: str
    content: str
    citations: list[str]


class ModelReport(BaseModel):
    """Provider-facing report. Status and sources are attached by the server."""

    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str
    sections: list[ModelSection]
    limitations: list[str]


class ReportBlock(BaseModel):
    """A short cited paragraph or table; kind distinguishes facts and inference."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fact", "inference", "context", "table"]
    text: str = Field(max_length=4000)
    citations: list[str] = Field(max_length=50)
    columns: list[str] = Field(max_length=12)
    rows: list[list[str]] = Field(max_length=30)

    @model_validator(mode="after")
    def valid_block(self) -> Self:
        if self.kind == "table":
            if (
                not self.columns
                or not self.rows
                or any(len(r) != len(self.columns) for r in self.rows)
            ):
                raise ValueError("table requires consistent columns and rows")
        elif not self.text.strip() or self.columns or self.rows:
            raise ValueError("paragraph requires text without table fields")
        if self.kind != "context" and not self.citations:
            raise ValueError("factual and analytical blocks require citations")
        return self


class ModelSectionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str = Field(min_length=1, max_length=200)
    blocks: list[ReportBlock] = Field(min_length=1, max_length=20)


class ModelReportV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    summary_blocks: list[ReportBlock] = Field(min_length=1, max_length=10)
    sections: list[ModelSectionV2] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(max_length=20)

    @model_validator(mode="after")
    def bounded_total(self) -> Self:
        if len(self.summary_blocks) + sum(len(s.blocks) for s in self.sections) > 120:
            raise ValueError("too many report blocks")
        return self


class ReportSection(BaseModel):
    """One section. Citations must name evidence selected for this run."""

    model_config = ConfigDict(extra="forbid")

    heading: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=30000)
    blocks: list[ReportBlock] = Field(default_factory=list, max_length=20)
    citations: list[str] = Field(default_factory=list, max_length=50)


class ResearchReport(BaseModel):
    """A structured report. Provenance comes from retrieval metadata.

    A citation id that matches a source means that passage was retrieved for
    this run. It does not prove the sentence is entailed by the excerpt.
    """

    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1, 2] = 1
    summary_blocks: list[ReportBlock] = Field(default_factory=list, max_length=10)
    verification: Literal["not_checked", "passed", "incomplete"] = "not_checked"
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=12000)
    sections: list[ReportSection] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    status: ReportStatus
    sources: list[EvidenceRef] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def citations_match_sources(self) -> Self:
        known = {source.evidence_id for source in self.sources}
        if len(known) != len(self.sources):
            raise ValueError("evidence ids must be unique")
        block_citations = [c for b in self.summary_blocks for c in b.citations]
        for section in self.sections:
            block_citations.extend(c for b in section.blocks for c in b.citations)
            for citation in section.citations:
                if citation not in known:
                    raise ValueError("citation does not match selected evidence")
        if any(c not in known for c in block_citations):
            raise ValueError("block citation does not match selected evidence")
        if (
            self.format_version == 2
            and self.status == "completed"
            and self.verification != "passed"
        ):
            raise ValueError("completed v2 report requires verification")
        if self.status == "insufficient_evidence" and (
            self.sources or any(section.citations for section in self.sections)
        ):
            raise ValueError("insufficient evidence cannot cite sources")
        return self


def _canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("must be a UUID") from None
    if str(parsed) != value:
        raise ValueError("must be a canonical UUID")
    return value
