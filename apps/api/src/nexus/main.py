"""FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from nexus import __version__
from nexus.api.v1.router import api_router
from nexus.core.config import Settings, get_settings
from nexus.infrastructure.database import create_db_engine, session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_db_engine(str(settings.database_url))
    app.state.engine = engine
    app.state.session_factory = session_factory(engine)
    try:
        yield
    finally:
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else get_settings()
    docs_enabled = resolved.environment == "development"
    app = FastAPI(
        title="Nexus Research API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        redoc_url=None,
    )
    app.state.settings = resolved
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
