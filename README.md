# Nexus Research

**An AI-powered deep research agent.**

Nexus Research is an experimental, Python-first application that investigates complex questions using web search, document retrieval, and an orchestrated AI workflow. Its goal is to produce structured research reports with traceable sources.

## Planned capabilities

- Break down research questions into actionable steps.
- Search the web and extract relevant information.
- Ingest PDF documents and retrieve supporting evidence with RAG.
- Orchestrate research with LangGraph and tool calling.
- Generate reports with source citations and visible execution progress.

## Planned stack

**Backend:** Python, FastAPI, Pydantic, SQLAlchemy, PostgreSQL  
**AI:** OpenAI, LangChain, LangGraph, Qdrant  
**Frontend:** Next.js  
**Tooling:** uv, pytest, Ruff, Docker Compose

## Status

🚧 **Phase 1 foundation is implemented.** The API, local PostgreSQL and Qdrant, Alembic, and health checks are in place. Product features listed above are still planned.

See the [development roadmap](docs/roadmap.md) for milestones and scope. Notes on the Python concepts from this phase are in [Phase 1 learning notes](docs/learning/phase-1.md).

## Purpose

A hands-on AI engineering project focused on Python, agent orchestration, retrieval-augmented generation, tool calling, and production-oriented development practices.

## Development

Phase 1 serves a versioned FastAPI application, local PostgreSQL and Qdrant, Alembic migrations, and readiness checks.

### Prerequisites

- Python 3.14
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker and Docker Compose, for the local PostgreSQL and Qdrant services

Install uv if it is not already on your `PATH`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Package bootstrap

Commands below run from `apps/api`.

```bash
cd apps/api
uv sync --frozen
uv run python -c "import nexus; print(nexus.__version__)"
```

`uv sync --frozen` installs the locked dependencies into `apps/api/.venv` and installs the `nexus` package in editable mode.

### For NestJS developers

| Python | NestJS / Node |
|---|---|
| `pyproject.toml` | `package.json` |
| `uv.lock` | the lockfile committed beside `package.json` |
| `.venv` | a local `node_modules` install |
| `src/nexus` | a compiled package whose imports resolve from the environment |

Import `nexus` as an installed package. `uv sync` puts it on the environment path, which is how application imports resolve.

`Settings` is process configuration loaded once at startup, the same role as Nest's `ConfigModule` validating the environment during boot. `async def` declares a route coroutine. The lifespan is the process startup and shutdown hook, the same role as `OnModuleInit` and `OnModuleDestroy`. `APIRouter` groups routes under `/api/v1`, the same role as a Nest module prefix. `Depends(get_session)` opens one SQLAlchemy `AsyncSession` for a request and closes it when the request finishes, including on failure. That session is one unit of work, the same role as a TypeORM `QueryRunner` created in request scope and released in `finally`. The handler owns the transaction and commits when it is ready. The liveness handler does no I/O.

### Run the API

From `apps/api`, copy the example environment and start the server. `.env` stays untracked.

```bash
cd apps/api
cp .env.example .env
uv run uvicorn nexus.main:app --reload
```

Uvicorn binds `127.0.0.1:8000` by default, which matches `NEXUS_HOST` and `NEXUS_PORT`. To use other values, pass the same `--host` and `--port` flags.

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health/live
curl -fsS http://127.0.0.1:8000/openapi.json
```

Liveness returns `{"status":"ok"}`. In `development`, OpenAPI is at `/openapi.json` and the docs UI is at `/docs`. A missing required variable such as `NEXUS_DATABASE_URL` stops the process before it serves requests and prints the variable name.

### Local data services

From the repository root, Compose starts PostgreSQL 18 and Qdrant. Both publish ports on `127.0.0.1` only. The database user, password, and name match `apps/api/.env.example` (`nexus` / `nexus` / `nexus`).

```bash
docker compose up -d
docker compose ps
docker compose exec postgres pg_isready -h 127.0.0.1 -U nexus -d nexus
curl -fsS http://127.0.0.1:6333/readyz
```

`pg_isready` checks PostgreSQL over TCP. `GET /readyz` is the Qdrant readiness probe. The Qdrant image does not ship an in-container health check.

`docker compose stop` stops the containers and keeps their named volumes. `docker compose down` removes the containers and keeps the volumes, so the next `up` restores the data. `docker compose down -v` deletes `nexus_postgres_data` and `nexus_qdrant_data`.

### Migrations

From `apps/api`, with Postgres running and `.env` in place:

```bash
cd apps/api
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
```

The initial revision creates Alembic's version table and no research tables. `upgrade head` applies that revision. Running it again leaves the database at the current head. `downgrade base` removes the version stamp. The lifespan creates the async engine when the API starts and disposes it on shutdown. It does not connect during startup, so liveness can be served while Postgres is stopped.

### Readiness

With the API running:

```bash
curl --max-time 5 -fsS http://127.0.0.1:8000/api/v1/health/ready
```

A ready process returns HTTP 200 and `{"status":"ok","postgres":"ok","qdrant":"ok"}`. If either service is down, the same URL returns HTTP 503 and `"status":"unavailable"`. Each check is `ok` or `unavailable`. The response does not include exception text or connection details. Liveness stays HTTP 200.

From the repository root, stop one service at a time. Both commands keep the named volumes:

```bash
docker compose stop postgres
docker compose start postgres
docker compose stop qdrant
docker compose start qdrant
```

Start Postgres again and wait until `pg_isready` succeeds before stopping Qdrant. The readiness handler checks both services together. PostgreSQL's connect and its `SELECT 1` are each limited by `NEXUS_READINESS_TIMEOUT_SECONDS`. Qdrant's HTTP call uses that same limit for the whole request. A failed PostgreSQL probe closes its connection and disposes its engine before the handler returns.

### Tests

From `apps/api`, with Compose running and `.env` present:

```bash
cd apps/api
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

`uv run pytest` runs the unit tests and the integration tests. Every test should pass. Integration tests create a database named `nexus_test`, migrate it, and drop it. They leave the development database `nexus` unchanged. `uv run pytest -m "not integration"` runs only the unit tests.

### Troubleshooting

- A missing `apps/api/.env`, or a missing `NEXUS_DATABASE_URL`, stops the process before it serves and prints the variable name. Copy `.env.example` to `.env`.
- Integration tests fail to connect when Compose is not running. Start it from the repository root with `docker compose up -d`.
- If port 5432 or 8000 is already taken, stop the other process. The published ports stay on `127.0.0.1` so they match `.env.example`.
- Stop the API with Ctrl+C in the uvicorn terminal. `docker compose stop` stops the data services and keeps their volumes.
