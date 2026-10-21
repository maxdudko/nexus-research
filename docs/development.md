# Development

This guide runs the API and Web directly with `uv` and `npm` against the development data services in `docker-compose.yml`. To run the packaged release stack instead, see [`docs/release/deployment.md`](release/deployment.md). The two setups use separate Compose projects and separate volumes.

## Prerequisites

- Python 3.14
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 24 and npm
- Docker and Docker Compose, for the local PostgreSQL and Qdrant services

Install uv if it is not already on your `PATH`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Package bootstrap

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

## Local data services

From the repository root, Compose starts PostgreSQL 18 and Qdrant. Both publish ports on `127.0.0.1` only. The database user, password, and name match `apps/api/.env.example` (`nexus` / `nexus` / `nexus`). These are development credentials; the release stack refuses the password `nexus`.

```bash
docker compose up -d
docker compose ps
docker compose exec postgres pg_isready -h 127.0.0.1 -U nexus -d nexus
curl -fsS http://127.0.0.1:6333/readyz
```

`pg_isready` checks PostgreSQL over TCP. `GET /readyz` is the Qdrant readiness probe. The development Compose file does not define a Qdrant health check.

Jaeger is optional and is not started by `docker compose up -d`. To collect traces locally:

```bash
docker compose --profile observability up -d jaeger
```

The UI is `http://127.0.0.1:16686`. Tracing is off unless `NEXUS_OTEL_ENABLED=true`. To send spans, also set `NEXUS_OTEL_ENDPOINT=http://127.0.0.1:4318/v1/traces` and restart the API. An exporter failure does not change the research result. Traces stay in Jaeger's memory and disappear when the container stops. Cost figures are estimates. Coverage stays partial when the OpenAI SDK may retry (`NEXUS_AI_MAX_RETRIES` defaults to 1), because those extra attempts are not visible on the successful response.

`docker compose stop` stops the containers and keeps their named volumes. `docker compose down` removes the containers and keeps the volumes, so the next `up` restores the data. `docker compose down -v` deletes `nexus_postgres_data` and `nexus_qdrant_data`.

## Run the API

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

Liveness returns `{"status":"ok"}`. In `development`, OpenAPI is at `/openapi.json` and the docs UI is at `/docs`. Both are disabled in `test` and `production`. A missing required variable such as `NEXUS_DATABASE_URL` stops the process before it serves requests and prints the variable name.

## Migrations and checkpoint tables

From `apps/api`, with Postgres running and `.env` in place:

```bash
cd apps/api
uv run alembic upgrade head
uv run python -m nexus.research.checkpoint
```

Alembic owns the domain schema (`sources`, `research_runs`, `research_reports`). Running `upgrade head` again leaves the database at the current head. `uv run alembic downgrade base` drops those tables and the version stamp.

LangGraph checkpoint tables are not managed by Alembic, and a request does not create them. `python -m nexus.research.checkpoint` creates or migrates them and is safe to run more than once. The release stack runs both commands in its one-shot `init` service.

The lifespan creates the async engine when the API starts and disposes it on shutdown. It does not connect during startup, so liveness can be served while Postgres is stopped.

## Readiness

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

## Run the web app

Start Compose, create the checkpoint tables, apply migrations, and start the API before the UI. The browser calls only `http://127.0.0.1:3000`. Next.js forwards `/api/v1` to `API_PROXY_TARGET`. Provider keys stay in the API environment. `apps/web/.env.example` contains only that proxy target.

```bash
cd apps/web
cp .env.example .env.local
npm ci
npm run dev
```

`API_PROXY_TARGET` defaults to `http://127.0.0.1:8000`. Open `http://127.0.0.1:3000`.

A text PDF is optional. The API accepts up to 10 MiB and 100 pages by default and does not store the original bytes. The same draft id is sent with the upload and with `POST /api/v1/research/stream`. When the stream finishes, the UI opens `/research/{run_id}` and loads the saved report. `https` citations open in a new tab. A PDF citation is a page label, not a file link. Refreshing that URL reads the domain row again.

Stop uses the label "Stop this request; research may be interrupted." That aborts the request. It does not resume later. A row left at `running` after a crash says execution is not confirmed. There is no authentication. Do not expose the API or the UI on the public internet. `research_id` is an evidence scope, not an access token.

A short local check, with provider keys only if you want a real report:

1. Ask a question of 10 to 2000 characters, with or without a text PDF.
2. Confirm a stage appears before the run finishes, then open the completed URL and refresh it.
3. Follow an `https` citation, or read a `PDF · page N` label.
4. Stop a second run and confirm the status is interrupted.
5. Open an older history page if one exists, and open a `running` row directly if one was left behind.

## Tests

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

From `apps/web`:

```bash
cd apps/web
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

### Offline evaluation

The offline evaluation command does not need an API key:

```bash
cd apps/api
uv run python -m nexus.evaluation --suite offline --output /tmp/nexus-eval-offline.json
diff -u ../../docs/evaluation/baseline.json /tmp/nexus-eval-offline.json
```

CI fails when that file differs from `docs/evaluation/baseline.json`. The numbers (Recall@5, Precision@5, MRR@5 on dataset `2026-10-19.2`) score frozen fixture rank lists. They do not measure production retrieval. The live judge is not part of CI. It calls OpenAI only when you set `NEXUS_RUN_LIVE_AI=1` and a key, and it prints the published `gpt-4o-mini` rates ($0.15 / 1M input tokens and $0.60 / 1M output tokens in pricing version `2026-10-19`). Those rates are estimates, not an invoice. See [`evaluation/methodology.md`](evaluation/methodology.md) and [`evaluation/failure-matrix.md`](evaluation/failure-matrix.md).

The Phase 3 retrieval fixture in `nexus.retrieval.evaluate` is a different, older check. Its Hit@5 and Recall@5 of 1.0 come from hashed-token vectors over the committed `retrieval_queries.json` fixture. That is a lexical check of the chunker and ranking arithmetic, not a `text-embedding-3-small` result and not part of `baseline.json`.

### Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs in two lanes. No job calls a paid provider.

| Trigger | Fast lane | Full lane |
| --- | --- | --- |
| Push to `development` | yes | no |
| Pull request to `master` | yes | yes |
| Push to `master` (after a merge) | yes | yes |
| Release tag `v*` ([`release.yml`](../.github/workflows/release.yml)) | yes | yes, plus release verification |
| Manual run (`workflow_dispatch`) | yes | only when `full` is checked |

Pushes to other branches do not trigger CI on their own. Open a pull request instead.

Fast lane, with no services and no Docker:

- **API checks:** Ruff, mypy, `pytest -m "not integration and not live"`, and the offline evaluation with the `baseline.json` diff.
- **Web checks:** `npm ci`, lint, typecheck and Vitest on Node 24.

Full lane. It starts only after the fast lane passes:

- **API integration:** starts PostgreSQL and Qdrant with `docker compose up -d`, runs `pytest -m "integration and not live"`, then `alembic upgrade head` twice.
- **Release images and smoke:** builds `nexus-api` and `nexus-web` tagged with `NEXUS_VERSION` from `.env.release.example` (the image build includes `next build`), runs `scripts/check_release_compose.py`, then `scripts/release-smoke.sh --no-build`. Scrubbed logs are uploaded only when the job fails. What the smoke does and does not prove is in [`release/demo.md`](release/demo.md).

A **Changes** job skips jobs whose paths did not change. API jobs need a change under `apps/api/`, Web jobs under `apps/web/`, and the image job under either. A change to `.github/`, `scripts/`, the Compose files, `.env.release.example` or `docs/evaluation/baseline.json` runs everything. A commit that touches only docs runs no checks. Tags and manual runs always run everything.

Branch protection should require only the **ci-ok** check. It passes when every job either succeeded or was skipped. Superseded runs on `development` and on pull requests are cancelled. Runs on `master` and on tags are never cancelled.

Caches: uv, npm, Ruff/mypy and the Docker layer cache (`type=gha`). GitHub only lets a run restore caches from its own ref or from `master`, so the full-lane run after each merge to `master` is what keeps pull-request builds warm. Pull requests write Docker layers to their own `pr-<number>-` scope, so they never evict the `master` entries.

Release tags run [`.github/workflows/release.yml`](../.github/workflows/release.yml). It runs `scripts/check_release_version.py --tag <tag>`, which checks that the tag, `pyproject.toml`, `package.json`, `package-lock.json`, the `compose.release.yml` default, `.env.release.example` and a dated `CHANGELOG.md` entry agree. It also checks that the tag is annotated and reachable from `master`. It then runs the full CI and uploads `release-images-<version>`: gzipped `docker save` tarballs, `image-ids.txt`, `commit.txt` and `SHA256SUMS`, kept for 90 days. Nothing is pushed to a registry. Run `python3 scripts/check_release_version.py` locally before tagging.

## Evidence

`RetrievalService` loads the accepted digest of each source in one `research_id`, embeds the query, and searches Qdrant with that filter on the server. The HTTP handler does not contain that logic.

`research_id` selects which evidence a query may see. It is not authentication or tenant isolation. Do not expose this API as a multi-user service without authorization.

A search score is cosine similarity between the query and a passage. It is not a probability that the passage is true. `POST /api/v1/evidence/search` returns the passages and their stored page number or URL. It does not call a chat model and it does not write an answer.

The original PDF bytes are not stored. Rebuilding a lost PDF index means uploading the file again. Scanned pages are not read. There is no keyword index and no reranker. Changing the chunk size or the point-id formula requires a new `NEXUS_RAG_COLLECTION`.

## Research plan

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

## Discovery

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

## Research run

`POST /api/v1/research/run` accepts a question and an optional `research_id`. The server always mints a new `run_id`. A client `run_id` or `thread_id` is 422 `invalid_request`. The same `research_id` on a later request is a new thread that can see evidence already indexed for that scope. It does not continue the previous thread.

The request waits until the graph finishes. There is no worker and no queue. `/run` does not stream. The product UI uses `POST /api/v1/research/stream`, described in [Run the web app](#run-the-web-app). `completed`, `partial`, and `insufficient_evidence` return 200 with the report, identifier warnings, and usage. `failed` and a hard deadline return the `{ "error": { "code", "message" } }` envelope and no report. A missing OpenAI key is 503 `provider_not_configured`. A checkpoint store that is down or not migrated is 503 `checkpoint_unavailable`. The message does not include the database URL.

`search_calls` and `llm_calls` count logical calls. A retry inside the OpenAI SDK or inside one Tavily search does not increase either number. A citation id means that passage was retrieved for this run. It does not prove the sentence is entailed by the excerpt.

A checkpoint row is durable state. It can be read after the pool is closed and a new checkpointer connects. LangGraph resumes a thread only when `ainvoke` is called again with that same thread id. This API never does that. A row left at `running` is abandoned, not a queued job.

Three stops are different:

- The graph deadline (`NEXUS_GRAPH_DEADLINE_SECONDS`, default 180) can still return 200 with a partial or insufficient report.
- The HTTP wait is that deadline plus 5 seconds. A proxy read timeout must sit above that sum; 200 seconds is a safe start. If this hard wait fires, the handler writes `interrupted` while the request is alive and returns 504 `deadline_exceeded` with no report.
- Closing the client, or killing the process, does not write a new status and does not continue the graph. The last completed node may stay `running`.

There is no resume API, no authentication, no durable PDF storage, and no checkpoint cleanup. Checkpoint tables are not a history API. A later run with the same `research_id` repeats Tavily and OpenAI calls. Fetching the same page again goes through Phase 3 ingestion, which keeps one source when the content is unchanged.

Index a PDF with `POST /api/v1/evidence/pdf` before the run, then pass that `research_id`. `/run` does not accept PDF bytes.

## Troubleshooting

- A missing `apps/api/.env`, or a missing `NEXUS_DATABASE_URL`, stops the process before it serves and prints the variable name. Copy `.env.example` to `.env`.
- Integration tests fail to connect when Compose is not running. Start it from the repository root with `docker compose up -d`.
- If port 5432 or 8000 is already taken, stop the other process. The published ports stay on `127.0.0.1` so they match `.env.example`.
- Stop the API with Ctrl+C in the uvicorn terminal. `docker compose stop` stops the data services and keeps their volumes.

## Learning notes

[Phase 1](learning/phase-1.md) (Python concepts), [Phase 2](learning/phase-2.md) (structured output, tool calling, search budgets), [Phase 3](learning/phase-3.md) (scoped retrieval), [Phase 4](learning/phase-4.md) (checkpoints and cancellation), [Phase 5](learning/phase-5.md) (product UI).
