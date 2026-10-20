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

🚧 **Phases 1–5 are implemented as a local research demo.** The API, local PostgreSQL and Qdrant, Alembic, and health checks are in place. `POST /api/v1/research/plan` returns a structured research plan from OpenAI. `POST /api/v1/research/discover` runs a bounded Tavily search and returns the snippets it retrieved. `POST /api/v1/evidence/pdf`, `POST /api/v1/evidence/web`, and `POST /api/v1/evidence/search` index a text PDF or one public HTML page and return scoped passages. `POST /api/v1/research/run` waits for one LangGraph run and returns a cited report, or the existing error envelope when the run fails. The Next.js app uses `POST /api/v1/research/stream` and the run history endpoints to show progress, reports, and citations.

See the [development roadmap](docs/roadmap.md) for milestones and scope. Notes on the Python concepts from Phase 1 are in [Phase 1 learning notes](docs/learning/phase-1.md). Notes on structured output, tool calling, and search budgets are in [Phase 2 learning notes](docs/learning/phase-2.md). Notes on scoped retrieval are in [Phase 3 learning notes](docs/learning/phase-3.md). Notes on checkpoints and cancellation are in [Phase 4 learning notes](docs/learning/phase-4.md). Notes on the product UI are in [Phase 5 learning notes](docs/learning/phase-5.md).

## Evidence

`RetrievalService` loads the accepted digest of each source in one `research_id`, embeds the query, and searches Qdrant with that filter on the server. The HTTP handler does not contain that logic. Phase 4 can call the service without importing the route.

`research_id` selects which evidence a query may see. It is not authentication or tenant isolation. Do not expose this API as a multi-user service without authorization.

A search score is cosine similarity between the query and a passage. It is not a probability that the passage is true. The response is the passages and their stored page number or URL. It does not call a chat model and it does not write an answer.

The checked-in Hit@5 of 1.0 is a lexical baseline over hashed tokens. It is not a `text-embedding-3-small` result. The opt-in live test is the OpenAI embedding check, and it does not report Hit@5.

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

### Run the web app

Start Compose, create the checkpoint tables, apply migrations, and start the API before the UI. The browser calls only `http://127.0.0.1:3000`. Next.js forwards `/api/v1` to `API_PROXY_TARGET`. Provider keys stay in the API environment. `apps/web/.env.example` contains only that proxy target.

```bash
cd apps/web
cp .env.example .env.local
npm install
npm run dev
```

`API_PROXY_TARGET` defaults to `http://127.0.0.1:8000`. Open `http://127.0.0.1:3000`.

A text PDF is optional. The API accepts up to 10 MiB and 100 pages by default and does not store the original bytes. The same draft id is sent with the upload and with `POST /api/v1/research/stream`. When the stream finishes, the UI opens `/research/{run_id}` and loads the saved report. `https` citations open in a new tab. A PDF citation is a page label, not a file link. Refreshing that URL reads the domain row again.

Stop uses the label “Stop this request; research may be interrupted.” That aborts the request. It does not resume later. A row left at `running` after a crash says execution is not confirmed. There is no authentication. Do not expose the API or the UI on the public internet. `research_id` is an evidence scope, not an access token.

A short local check, with provider keys only if you want a real report:

1. Ask a question of 10 to 2000 characters, with or without a text PDF.
2. Confirm a stage appears before the run finishes, then open the completed URL and refresh it.
3. Follow an `https` citation, or read a `PDF · page N` label.
4. Stop a second run and confirm the status is interrupted.
5. Open an older history page if one exists, and open a `running` row directly if one was left behind.

### Local data services

From the repository root, Compose starts PostgreSQL 18 and Qdrant. Both publish ports on `127.0.0.1` only. The database user, password, and name match `apps/api/.env.example` (`nexus` / `nexus` / `nexus`).

```bash
docker compose up -d
docker compose ps
docker compose exec postgres pg_isready -h 127.0.0.1 -U nexus -d nexus
curl -fsS http://127.0.0.1:6333/readyz
```

`pg_isready` checks PostgreSQL over TCP. `GET /readyz` is the Qdrant readiness probe. The Qdrant image does not ship an in-container health check.

Jaeger is optional and is not started by `docker compose up -d`. To collect traces locally:

```bash
docker compose --profile observability up -d jaeger
```

The UI is `http://127.0.0.1:16686`. Tracing is off unless `NEXUS_OTEL_ENABLED=true`. To send spans, also set `NEXUS_OTEL_ENDPOINT=http://127.0.0.1:4318/v1/traces` and restart the API. An exporter failure does not change the research result. Traces stay in Jaeger's memory and disappear when the container stops. Cost figures are estimates. Coverage stays partial when the OpenAI SDK may retry (`NEXUS_AI_MAX_RETRIES` defaults to 1), because those extra attempts are not visible on the successful response.

`docker compose stop` stops the containers and keeps their named volumes. `docker compose down` removes the containers and keeps the volumes, so the next `up` restores the data. `docker compose down -v` deletes `nexus_postgres_data` and `nexus_qdrant_data`.

### Release stack (preview)

`compose.release.yml` runs Web, API, PostgreSQL and Qdrant from production images, without local Python or Node. It is a separate Compose project (`nexus-release`) with its own volumes. The development commands above are unchanged.

```bash
cp .env.release.example .env.release   # set POSTGRES_PASSWORD (openssl rand -hex 32) and provider keys
docker compose --env-file .env.release -f compose.release.yml config --quiet
docker compose --env-file .env.release -f compose.release.yml up -d --build
docker compose --env-file .env.release -f compose.release.yml ps
python3 scripts/check_release_compose.py   # static port, network and secret-scoping checks
```

The one-shot `init` service runs `alembic upgrade head` and then the checkpoint setup on every `up`. Both are idempotent. The API starts only after `init` exits 0 and the data services are healthy. Only Web is published, on `http://127.0.0.1:3000`. API, PostgreSQL and Qdrant have no host ports. There is no authentication, so do not expose the port publicly. `down` keeps the release volumes; `down -v` deletes them. Full deployment and operations guides come with the v0.1.0 documentation.

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

GitHub Actions runs these commands on every push and pull request. The workflow starts PostgreSQL and Qdrant with Docker Compose, copies `.env.example` to `.env`, then runs Ruff, mypy, pytest, the offline evaluation command below, and `alembic upgrade head` twice.

The offline evaluation command does not need an API key:

```bash
uv run python -m nexus.evaluation --suite offline --output /tmp/nexus-eval-offline.json
```

CI fails when that file differs from `docs/evaluation/baseline.json`. The numbers score the frozen fixtures. They do not measure production retrieval. The live judge is not part of CI. It calls OpenAI only when you set `NEXUS_RUN_LIVE_AI=1` and a key, and it prints the published `gpt-4o-mini` rates ($0.15 / 1M input tokens and $0.60 / 1M output tokens in pricing version `2026-10-19`). Those rates are estimates, not an invoice. The live embedding search is also outside CI and is not compared with `baseline.json`. See `docs/evaluation/methodology.md` and `docs/evaluation/failure-matrix.md`.

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

### Research run

`POST /api/v1/research/run` accepts a question and an optional `research_id`. The server always mints a new `run_id`. A client `run_id` or `thread_id` is 422 `invalid_request`. The same `research_id` on a later request is a new thread that can see evidence already indexed for that scope. It does not continue the previous thread.

The request waits until the graph finishes. There is no worker and no queue. `/run` does not stream. The product UI uses `POST /api/v1/research/stream`, described in [Run the web app](#run-the-web-app). `completed`, `partial`, and `insufficient_evidence` return 200 with the report, identifier warnings, and usage. `failed` and a hard deadline return the `{ "error": { "code", "message" } }` envelope and no report. A missing OpenAI key is 503 `provider_not_configured`. A checkpoint store that is down or not migrated is 503 `checkpoint_unavailable`. The message does not include the database URL.

`search_calls` and `llm_calls` count logical calls. A retry inside the OpenAI SDK or inside one Tavily search does not increase either number. A citation id means that passage was retrieved for this run. It does not prove the sentence is entailed by the excerpt.

A checkpoint row is durable state. It can be read after the pool is closed and a new checkpointer connects. LangGraph resumes a thread only when `ainvoke` is called again with that same thread id. This API never does that. A row left at `running` is abandoned, not a queued job.

Three stops are different:

- The graph deadline (`NEXUS_GRAPH_DEADLINE_SECONDS`, default 180) can still return 200 with a partial or insufficient report.
- The HTTP wait is that deadline plus 5 seconds. A proxy read timeout must sit above that sum; 200 seconds is a safe start. If this hard wait fires, the handler writes `interrupted` while the request is alive and returns 504 `deadline_exceeded` with no report.
- Closing the client, or killing the process, does not write a new status and does not continue the graph. The last completed node may stay `running`.

There is no resume API, no authentication, no durable PDF storage, and no checkpoint cleanup. Checkpoint tables are not a history API. A later run with the same `research_id` repeats Tavily and OpenAI calls. Fetching the same page again goes through Phase 3 ingestion, which keeps one source when the content is unchanged.

Create those tables once. Alembic does not own them, and a request does not migrate them:

```bash
cd apps/api
uv run python -m nexus.research.checkpoint
```

Index a PDF with `POST /api/v1/evidence/pdf` before the run, then pass that `research_id`. `/run` does not accept PDF bytes.

### Troubleshooting

- A missing `apps/api/.env`, or a missing `NEXUS_DATABASE_URL`, stops the process before it serves and prints the variable name. Copy `.env.example` to `.env`.
- Integration tests fail to connect when Compose is not running. Start it from the repository root with `docker compose up -d`.
- If port 5432 or 8000 is already taken, stop the other process. The published ports stay on `127.0.0.1` so they match `.env.example`.
- Stop the API with Ctrl+C in the uvicorn terminal. `docker compose stop` stops the data services and keeps their volumes.
