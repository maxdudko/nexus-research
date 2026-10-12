# Nexus Research — Phase 1: Python Backend Foundation

**Status:** Proposed  
**Version:** 1.0  
**Date:** 2026-10-10  
**Scope:** Backend foundation only

## 1. Context

Nexus Research is a Python-first autonomous research product. Its eventual MVP combines OpenAI-powered web research, PDF ingestion, RAG, LangChain/LangGraph orchestration, and a Next.js UI. Phase 1 establishes a reproducible, testable backend foundation without implementing AI workflows.

## 2. Objectives

- Bootstrap an idiomatic Python 3.14 application managed by uv.
- Expose a versioned FastAPI application with clear lifecycle and dependency boundaries.
- Configure PostgreSQL persistence with SQLAlchemy 2.0.x async APIs and Alembic migrations.
- Run PostgreSQL 18 and Qdrant locally using Docker Compose.
- Establish validated settings, linting, typing, and unit/integration tests.
- Provide setup documentation understandable to a TypeScript/NestJS developer learning Python.

## 3. Functional requirements

### FR-01 — Project bootstrap
- The Python package resides in `apps/api`, using the `src/nexus` package layout.
- `pyproject.toml` declares runtime/development dependencies, Python version constraints, Ruff, mypy and pytest configuration.
- `uv.lock` is committed and `uv sync --frozen` is reproducible.
- Imports work through package installation, not `PYTHONPATH` hacks.

### FR-02 — HTTP API
- `GET /api/v1/health/live` returns HTTP 200 with `{"status":"ok"}` without requiring database access.
- `GET /api/v1/health/ready` checks PostgreSQL connectivity and Qdrant availability, returning HTTP 200 when both are ready and HTTP 503 otherwise.
- API exposes `/docs` and `/openapi.json` in development.
- API routes do not instantiate database clients directly.

### FR-03 — Configuration
- Settings use `pydantic-settings`, loaded from environment variables and optional local `.env`.
- Configuration includes environment, host/port, database URL, Qdrant URL, and readiness timeouts.
- `.env.example` contains placeholders and safe local defaults, no live secrets.
- OpenAI API key is reserved as optional configuration for later phases; Phase 1 does not require or call OpenAI.
- Invalid required configuration fails with an actionable error at startup.

### FR-04 — Database
- Use SQLAlchemy 2.0.x async engine, `asyncpg`, and `async_sessionmaker`.
- Engine is initialized during application lifespan and disposed on shutdown.
- Session dependencies yield a new session per request and always close it.
- Never share an `AsyncSession` across concurrent tasks.
- Alembic has a working initial revision; no speculative research domain tables are required in Phase 1.
- The migration command works against an empty local PostgreSQL database.

### FR-05 — Local infrastructure
- Docker Compose runs PostgreSQL 18 and Qdrant with persistent named volumes and health checks where supported.
- API runs locally using uv; containerizing the API is optional for this phase.
- Database and Qdrant ports bind to localhost for local development.
- A documented reset procedure distinguishes data-preserving stop from volume deletion.

### FR-06 — Tests and quality
- Unit tests cover settings validation and liveness behavior.
- Integration tests cover readiness success/failure and real PostgreSQL connectivity.
- External Qdrant availability is tested with a local container; simulated failures use dependency overrides or injected clients.
- Tests use isolated database resources and do not mutate the development database.
- `ruff check`, `ruff format --check`, `mypy`, and `pytest` all pass.

### FR-07 — Documentation
- README documents prerequisites, setup, startup, migrations, health checks, tests, troubleshooting, and shutdown.
- Document core Python concepts introduced: packages, type hints, async context managers, dependency injection, and async session lifecycle.

## 4. Non-functional requirements

- No blocking network/database I/O in async request handlers.
- Explicit connect/readiness timeouts; no hanging health checks.
- Secrets are not committed or logged.
- Database failures produce a safe HTTP response, not a stack trace to clients.
- All dependencies have explicit compatible version constraints; lockfile records exact resolutions.
- Code remains modular without introducing generic repository frameworks or unnecessary abstractions.

## 5. Out of scope

- OpenAI calls, LangChain, LangGraph, web search, PDF parsing, embeddings, RAG, agent loops.
- Authentication, billing, tenancy, production workers, SSE, frontend, Redis, Celery.
- Research domain tables or premature schema design.
- Production deployment, autoscaling, Kubernetes, distributed tracing.

## 6. Acceptance criteria

1. A fresh checkout can follow the README to install Python dependencies with uv.
2. `docker compose up -d` starts PostgreSQL and Qdrant successfully.
3. `uv run alembic upgrade head` succeeds on a clean database.
4. `uv run uvicorn nexus.main:app --reload` starts the API.
5. `/api/v1/health/live` returns 200 regardless of dependency health.
6. `/api/v1/health/ready` returns 200 with both services available and 503 if either is unavailable.
7. OpenAPI documentation is available in development.
8. `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, and `uv run pytest` pass.
9. Integration tests use isolated test data/resources.
10. No secrets or runtime-generated files are tracked by Git.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Async SQLAlchemy session misuse | One session per request/task; explicit ownership and cleanup |
| Database readiness flakiness | Timeouts, health checks, controlled startup, deterministic test setup |
| Migration setup complexity | Minimal initial revision, documented upgrade and rollback commands |
| Dependency API drift | Version constraints + committed uv.lock; verify official docs |
| Scope creep | No AI integration or domain schema in Phase 1 |

## 8. Learning outcomes

By the end of Phase 1, the developer should be able to explain: `pyproject.toml` and uv; `src/` layout; `async def` vs `def`; FastAPI lifespan and `Depends`; Pydantic settings; SQLAlchemy async engine/session lifecycle; Alembic migration mechanics; pytest fixtures and dependency overrides.

## 9. Definition of done

All acceptance criteria pass in a clean local environment; documentation is reproducible; code review reveals no Phase 2+ features implemented prematurely.
