"""Logical graph counts for a time window. There is no cost column."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.admin.diagnostics import resolve_window
from nexus.models.run import ResearchRun
from nexus.schemas.report import GraphUsage


class UsageCounters(BaseModel):
    """Sums of valid graph usage. Tokens and retries are not fields."""

    model_config = ConfigDict(extra="forbid")

    iterations: int = Field(ge=0)
    search_calls: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    pages_indexed: int = Field(ge=0)
    pages_failed: int = Field(ge=0)


class UsageSummary(BaseModel):
    """Usage for one window. Cost stays unknown rather than zero."""

    model_config = ConfigDict(extra="forbid")

    window_start: datetime
    window_end: datetime
    runs_with_usage: int = Field(ge=0)
    runs_without_usage: int = Field(ge=0)
    counters: UsageCounters
    cost_estimate: None = None
    coverage: Literal["unavailable"] = "unavailable"
    pricing_version: None = None


async def usage_summary(
    factory: async_sessionmaker[AsyncSession],
    now: datetime,
    start: datetime | None,
    end: datetime | None,
) -> UsageSummary:
    """Sum valid usage objects. Invalid JSON is counted and not returned."""
    opened, closed = resolve_window(now, start, end)
    async with factory() as session:
        rows = (
            await session.scalars(
                select(ResearchRun.usage).where(
                    ResearchRun.created_at >= opened,
                    ResearchRun.created_at < closed,
                )
            )
        ).all()
    totals = {
        "iterations": 0,
        "search_calls": 0,
        "llm_calls": 0,
        "pages_indexed": 0,
        "pages_failed": 0,
    }
    present = 0
    missing = 0
    for raw in rows:
        try:
            usage = GraphUsage.model_validate(raw)
        except ValidationError:
            missing += 1
            continue
        present += 1
        totals["iterations"] += usage.iterations
        totals["search_calls"] += usage.search_calls
        totals["llm_calls"] += usage.llm_calls
        totals["pages_indexed"] += usage.pages_indexed
        totals["pages_failed"] += usage.pages_failed
    return UsageSummary(
        window_start=opened,
        window_end=closed,
        runs_with_usage=present,
        runs_without_usage=missing,
        counters=UsageCounters.model_validate(totals),
        cost_estimate=None,
        coverage="unavailable",
        pricing_version=None,
    )
