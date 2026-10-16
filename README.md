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

🚧 **Phase 1 foundation, Phase 2 planning and web search, and Phase 3 evidence ingestion and search are implemented.** The API, local PostgreSQL and Qdrant, Alembic, and health checks are in place. `POST /api/v1/research/plan` returns a structured research plan from OpenAI. `POST /api/v1/research/discover` runs a bounded Tavily search and returns the snippets it retrieved. `POST /api/v1/evidence/pdf`, `POST /api/v1/evidence/web`, and `POST /api/v1/evidence/search` index a text PDF or one public HTML page and return scoped passages. LangGraph is a later phase.

See the [development roadmap](docs/roadmap.md) for milestones and scope. Notes on the Python concepts from Phase 1 are in [Phase 1 learning notes](docs/learning/phase-1.md). Notes on structured output, tool calling, and search budgets are in [Phase 2 learning notes](docs/learning/phase-2.md). Notes on scoped retrieval are in [Phase 3 learning notes](docs/learning/phase-3.md).

## Evidence

`RetrievalService` loads the accepted digest of each source in one `research_id`, embeds the query, and searches Qdrant with that filter on the server. The HTTP handler does not contain that logic. Phase 4 can call the service without importing the route.

`research_id` selects which evidence a query may see. It is not authentication or tenant isolation. Do not expose this API as a multi-user service without authorization.

A search score is cosine similarity between the query and a passage. It is not a probability that the passage is true. The response is the passages and their stored page number or URL. It does not call a chat model and it does not write an answer.

The original PDF bytes are not stored. Rebuilding a lost PDF index means uploading the file again. Scanned pages are not read. There is no keyword index and no reranker. Changing the chunk size or the point-id formula requires a new `NEXUS_RAG_COLLECTION`.

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

`upgrade head` creates the `sources` table used by evidence ingestion. Running it again leaves the database at the current head. `downgrade base` drops that table and the version stamp. The lifespan creates the async engine when the API starts and disposes it on shutdown. It does not connect during startup, so liveness can be served while Postgres is stopped.

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

`uv run pytest` runs the unit tests and the integration tests. Every test should pass. Integration tests create a database named `nexus_test`, migrate it, and drop it. They leave the development database `nexus` unchanged. The fixture only does that when the host is `localhost`, `127.0.0.1`, or `::1`, and it stops before any `DROP DATABASE` if `NEXUS_DATABASE_URL` already uses `nexus_test`. `uv run pytest -m "not integration"` runs only the unit tests.

GitHub Actions runs these commands on every push and pull request. The workflow starts PostgreSQL and Qdrant with Docker Compose, copies `.env.example` to `.env`, then runs Ruff, mypy, pytest, and `alembic upgrade head` twice.

### Research plan

`POST /api/v1/research/plan` accepts a question of 10–2000 characters and returns a structured plan. The API still starts when `NEXUS_OPENAI_API_KEY` is empty. Health checks do not call OpenAI. A plan request without that key returns HTTP 503 and `provider_not_configured`.

Copy `apps/api/.env.example` to `apps/api/.env` and set a key only on a machine where a paid call is acceptable. `NEXUS_OPENAI_MODEL` selects the model. The example value is `gpt-4o-mini`, which is OpenAI's current starting model for strict `json_schema` output. Do not point it at a `*-chat-latest` alias; some of those aliases reject `json_schema`. Each plan call uses that model once, waits up to `NEXUS_AI_TIMEOUT_SECONDS` (default 20), and lets the OpenAI SDK retry up to `NEXUS_AI_MAX_RETRIES` times (default 1, maximum 2). There is no second retry loop in the application.

With the API running:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/research/plan \
  -H 'Content-Type: application/json' \
  -d '{"question":"How do AI coding agents improve software development workflows?"}'
```

A successful response has `objective` and `tasks`. Each task has `question` and `search_queries`. The plan is not a finished report, and it does not mean a search already ran.

Failures use `{ "error": { "code", "message" } }`:

| HTTP | code | When |
|---|---|---|
| 422 | `invalid_request` | The question is missing or outside 10–2000 characters |
| 429 | `provider_rate_limited` | OpenAI rate-limits the call |
| 503 | `provider_not_configured` | `NEXUS_OPENAI_API_KEY` is empty |
| 503 | `provider_unavailable` | Authentication, connection, or another provider failure |
| 503 | `model_refused` | The model refuses the question |
| 503 | `invalid_model_output` | The model output is empty or fails the plan bounds |
| 504 | `provider_timeout` | The provider call times out |

Messages are fixed. They do not include the key, the prompt, or the provider body. An invalid plan is rejected rather than rewritten into a successful response.

Default `uv run pytest` does not call OpenAI. The live check is opt-in and can incur a charge:

```bash
cd apps/api
NEXUS_RUN_LIVE_AI=1 NEXUS_OPENAI_API_KEY=sk-... uv run pytest -m live
```

The test still skips unless both `NEXUS_RUN_LIVE_AI=1` and `NEXUS_OPENAI_API_KEY` are set. It asks for one research plan and checks that the result matches `ResearchPlan`.

### Discovery

`POST /api/v1/research/discover` takes the same question. The model may request `web_search`. The API runs Tavily, sends the snippets back, and returns the sources it actually retrieved. Those snippets are search results, not fetched pages. A missing OpenAI key or a missing `NEXUS_TAVILY_API_KEY` returns 503 `provider_not_configured`. Health checks still do not call either provider.

A plan request is one model call. A discover request is at most 4 model calls and 3 Tavily searches, with 5 results per search. The whole request stops after `NEXUS_DISCOVERY_TIMEOUT_SECONDS` (default 90). Default `uv run pytest` and GitHub Actions do not call either provider. The live command below can charge both.

A successful or partial response looks like this. The values are a contract example, not a verified source:

```json
{
  "status": "completed",
  "summary": "The retrieved sources discuss agent-assisted code review and testing.",
  "sources": [
    {
      "title": "Example source",
      "url": "https://example.org/research",
      "snippet": "A retrieved snippet from Tavily.",
      "published_at": null
    }
  ],
  "search_calls": 1,
  "llm_calls": 2,
  "warnings": []
}
```

`status` is `completed` or `partial`. `completed` means the tool loop stopped inside the budget and the summary cites only retrieved URLs. It does not fact-check the summary, and it does not mean the pages were fetched. `sources` is the provenance: each item keeps the title, URL, and snippet returned by search. `published_at` is null unless Tavily returned a parseable timestamp. `search_calls` and `llm_calls` count logical calls. A retry inside one Tavily search or one model call does not increase either number.

These warnings stay on HTTP 200:

| warning | When |
|---|---|
| `insufficient_sources` | Nothing was retrieved. The summary does not answer the question |
| `ungrounded_summary` | The model summary cites a URL that was not retrieved |
| `budget_exhausted` | The search cap or the model cap was hit |
| `search_failed` | One Tavily timeout, connection failure, or malformed payload stayed inside the loop |
| `malformed_tool_call` | A call had no id, or two calls shared an id, so that batch was not searched |

Provider failures use the same `{ "error": { "code", "message" } }` envelope as planning:

| HTTP | code | When |
|---|---|---|
| 422 | `invalid_request` | The question is missing or outside 10–2000 characters |
| 429 | `provider_rate_limited` | Tavily or OpenAI rate-limits the call |
| 503 | `provider_not_configured` | The OpenAI key or the Tavily key is empty |
| 503 | `provider_unavailable` | The Tavily key is invalid, or another provider failure |
| 504 | `provider_timeout` | The overall deadline or an OpenAI call times out |

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/research/discover \
  -H 'Content-Type: application/json' \
  -d '{"question":"How do AI coding agents improve software development workflows?"}'
```

A discover live test also calls OpenAI and Tavily. It stays out of default pytest and CI. Set both keys:

```bash
cd apps/api
NEXUS_RUN_LIVE_AI=1 \
  NEXUS_OPENAI_API_KEY=sk-... \
  NEXUS_TAVILY_API_KEY=tvly-... \
  uv run pytest -m live
```

### Troubleshooting

- A missing `apps/api/.env`, or a missing `NEXUS_DATABASE_URL`, stops the process before it serves and prints the variable name. Copy `.env.example` to `.env`.
- Integration tests fail to connect when Compose is not running. Start it from the repository root with `docker compose up -d`.
- If port 5432 or 8000 is already taken, stop the other process. The published ports stay on `127.0.0.1` so they match `.env.example`.
- Stop the API with Ctrl+C in the uvicorn terminal. `docker compose stop` stops the data services and keeps their volumes.
