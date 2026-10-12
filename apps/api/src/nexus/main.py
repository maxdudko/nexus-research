"""FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from nexus import __version__
from nexus.api.v1.router import api_router
from nexus.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.settings = get_settings()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    docs_enabled = settings.environment == "development"
    app = FastAPI(
        title="Nexus Research API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        redoc_url=None,
    )
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
