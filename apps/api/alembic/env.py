"""Alembic migration environment."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from nexus.core.config import get_settings
from nexus.infrastructure.database import create_db_engine
from nexus.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# nexus.models imports every ORM table, including sources and research runs.
target_metadata = Base.metadata


def _database_url() -> str:
    return str(get_settings().database_url)


def run_migrations_offline() -> None:
    """Emit SQL using the application database URL."""
    url = _database_url()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = _database_url()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    connectable = create_db_engine(url, null_pool=True)
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against the configured async database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
