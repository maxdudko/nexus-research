# syntax=docker/dockerfile:1
# Production API image. Build context: apps/api.
# Refresh pins with `docker buildx imagetools inspect <image>:<tag>` and update
# tag and digest together.

ARG PYTHON_IMAGE=python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
# The virtualenv path must match the runtime stage: entry-point shebangs are absolute.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.14
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 nexus \
    && useradd --system --uid 10001 --gid nexus --no-create-home --shell /usr/sbin/nologin nexus
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Alembic resolves its scripts relative to alembic.ini in the working directory.
COPY alembic.ini ./
COPY alembic ./alembic
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXUS_ENVIRONMENT=production
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "nexus.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10"]
