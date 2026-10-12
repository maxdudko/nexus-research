# Nexus Research — Phase 1 Implementation Plan

**Status:** Ready for implementation  
**Estimate:** 2 focused development days (flexible; learning and review may take longer)  
**Prerequisite:** Approved `spec.md` and `ADR-001.md`

## Execution rules

- Work in small commits, one step at a time.
- Before each step, explain the relevant Python concepts and the equivalent NestJS/TypeScript pattern.
- Present proposed files and commands before making major changes.
- Run checks after each step; do not claim passing tests without executing them.
- Do not add AI/agent functionality or speculative abstractions.

## Step 0 — Inspect and establish baseline

**Tasks**
- Inspect repository and existing configuration; do not overwrite unrelated files.
- Check installed Python, uv, Docker and Compose versions.
- Confirm supported Python 3.14 and dependency versions.
- Confirm target directory structure and naming.

**Done when:** Current state, constraints and implementation sequence are documented.

## Step 1 — Python package bootstrap

**Tasks**
- Create `apps/api/pyproject.toml` and `src/nexus/__init__.py`.
- Configure Python >=3.14,<3.15, uv lockfile, dependency groups.
- Add `.gitignore`, `.env.example`, initial README.
- Configure Ruff, mypy and pytest in `pyproject.toml`.
- Install package in editable mode through uv.

**Validation:** `uv sync`, `uv run python -c "import nexus"`, `uv run ruff check .`.

**Learn:** Python packaging, imports, virtual environments, pyproject vs package.json, lockfiles.

## Step 2 — FastAPI application and configuration

**Tasks**
- Create `core/config.py` using `BaseSettings`.
- Create `main.py` with lifespan setup and versioned API router.
- Add `/api/v1/health/live` and automatic OpenAPI docs.
- Keep configuration validation separate from request handling.

**Validation:** Start uvicorn; verify liveness 200 and `/openapi.json`.

**Learn:** `async def`, Pydantic models, `Depends`, lifespan, Python module imports.

## Step 3 — Local PostgreSQL and Qdrant

**Tasks**
- Add `docker-compose.yml` with PostgreSQL 18 and Qdrant.
- Use named volumes and localhost-only port bindings.
- Add PostgreSQL health check and documented Qdrant readiness probe.
- Document setup, startup, stop, and destructive reset commands.

**Validation:** Containers start; database and Qdrant endpoints are reachable.

**Learn:** Containers, local networking, persistence, service readiness.

## Step 4 — Async persistence and migrations

**Tasks**
- Configure SQLAlchemy async engine and `async_sessionmaker`.
- Add session dependency with guaranteed cleanup.
- Initialize/dispose engine using lifespan.
- Configure Alembic and create a minimal initial revision without speculative domain tables.
- Make migration command work from `apps/api`.

**Validation:** `uv run alembic upgrade head`; database query `SELECT 1`; repeated migration is idempotent.

**Learn:** async context managers, sessions, transactions, SQLAlchemy vs TypeORM, migrations.

## Step 5 — Readiness and failure handling

**Tasks**
- Implement `/api/v1/health/ready` with bounded PostgreSQL and Qdrant checks.
- Return HTTP 503 on unavailable dependencies.
- Keep liveness independent of downstream services.
- Avoid exposing credentials, internal exception messages or stack traces in responses.

**Validation:** 200 with healthy services; 503 with PostgreSQL down; 503 with Qdrant down; liveness remains 200.

**Learn:** exception handling, dependency injection, async timeouts, service health semantics.

## Step 6 — Tests and developer experience

**Tasks**
- Unit tests for settings and liveness.
- Integration tests for database connectivity and readiness.
- Use separate test database and safe fixtures/cleanup.
- Add dependency overrides for simulated service failures.
- Configure lint, formatting, type checking and test commands.
- Document expected output and troubleshooting.

**Validation:**

```bash
cd apps/api
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

**Learn:** pytest fixtures, parametrization, async testing, mocking, dependency overrides.

## Step 7 — Final review and handoff

**Tasks**
- Verify all Phase 1 acceptance criteria against a clean local setup.
- Inspect dependency tree and ensure no LangChain/LangGraph/agent code has slipped in.
- Review `.gitignore`, secrets handling, README, and lockfile.
- Summarize architecture decisions, technical debt and Phase 2 prerequisites.
- Create a concise learning journal entry with five questions and answers.

**Done when:** All criteria pass and the project is ready for OpenAI + tool-calling integration.

## Suggested commit sequence

1. `chore(api): bootstrap python package and quality tools`
2. `feat(api): add fastapi app and settings`
3. `chore(infra): add postgres and qdrant compose services`
4. `feat(api): configure async database and alembic`
5. `feat(api): add dependency readiness checks`
6. `test(api): cover configuration and infrastructure health`
7. `docs(api): finalize phase one setup and learning notes`

## Phase 1 → Phase 2 handoff

Phase 2 can begin only after the API is reproducible and healthy. The next scope is OpenAI integration, Pydantic structured outputs, and an initial web-search tool; LangGraph orchestration and PDF RAG follow in later phases.
