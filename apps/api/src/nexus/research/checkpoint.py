"""PostgreSQL checkpoints. Setup is explicit; a request never migrates."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine.url import make_url

from nexus.research.graph import GraphContext, build_graph
from nexus.research.state import ResearchState

logger = logging.getLogger(__name__)


@dataclass
class CheckpointRuntime:
    """One pool, its saver, and the graph compiled against that saver."""

    pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]]
    saver: AsyncPostgresSaver
    graph: CompiledStateGraph[ResearchState, GraphContext, ResearchState, ResearchState]


def libpq_conninfo(database_url: str) -> str:
    """A psycopg URI. The asyncpg driver name is not part of libpq."""
    return (
        make_url(database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


async def setup_checkpoint(database_url: str) -> None:
    """Create or migrate the LangGraph tables. Safe to run more than once."""
    async with AsyncPostgresSaver.from_conn_string(
        libpq_conninfo(database_url)
    ) as saver:
        await saver.setup()


async def open_checkpointer(database_url: str) -> CheckpointRuntime:
    """Open one pool and compile the graph. The caller closes the pool.

    A later saver can read checkpoints written here. Opening the pool does not
    resume a thread, and this function does not call ``setup()``.
    """
    pool = AsyncConnectionPool[AsyncConnection[dict[str, Any]]](
        conninfo=libpq_conninfo(database_url),
        min_size=1,
        max_size=10,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    try:
        await pool.open()
        saver = AsyncPostgresSaver(pool)
        graph = build_graph(saver)
    except BaseException:
        await pool.close()
        raise
    return CheckpointRuntime(pool=pool, saver=saver, graph=graph)


def main() -> None:
    """Migrate checkpoint tables for the configured database."""
    asyncio.run(_main())


async def _main() -> None:
    from nexus.core.config import get_settings

    settings = get_settings()
    try:
        await setup_checkpoint(str(settings.database_url))
    except Exception as exc:
        logger.error("checkpoint setup failed: %s", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
