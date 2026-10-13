# Phase 1 learning notes

Phase 1 is a runnable API foundation. It does not research anything yet.

The `nexus` package lives in `apps/api/src/nexus`. `create_app` builds a FastAPI application, stores `Settings` on `app.state`, and mounts routes under `/api/v1`. The lifespan creates one async SQLAlchemy engine and a session factory, then disposes the engine on shutdown. It does not ping PostgreSQL, so liveness stays available when the database is down. Readiness asks PostgreSQL for `SELECT 1` and Qdrant for `GET /readyz`. PostgreSQL limits the connect and the query separately with the driver timeout, then invalidates that connection and disposes the probe engine before returning. Qdrant uses one HTTP timeout. Either failure returns 503. Docker Compose runs PostgreSQL 18 and Qdrant on localhost. Alembic's first revision records a version and creates no research tables.

`uv tree` includes `opentelemetry-api` because FastAPI 0.143 requires it. It does not include `opentelemetry-sdk`, `langchain`, `langgraph`, `openai`, or `qdrant-client`.

## Questions

### Why do `pyproject.toml` and `uv.lock` exist, and why install `nexus` instead of setting `PYTHONPATH`?

`pyproject.toml` declares the project: the package name, the Python version, and the allowed dependency ranges. `uv.lock` records the exact versions that satisfied those ranges, so the next install is the same one. `uv sync --frozen` installs that lock into `apps/api/.venv` and installs `nexus` itself in editable mode. Imports then resolve as `import nexus` from the environment, the same way a published package would. A `PYTHONPATH` pointing at `src` only works in the shell that set it, and it hides the fact that the project is a real package.

### When does a route use `async def` instead of `def`?

`async def` means the function is a coroutine. FastAPI runs it on the event loop and can await other coroutines without blocking that loop. The liveness handler is a coroutine even though it does no I/O:

```python
@router.get("/live")
async def live() -> LiveStatus:
    return LiveStatus(status="ok")
```

A plain `def` route is for work that is already synchronous. FastAPI runs those in a thread pool. Liveness is declared `async def` so it stays on the event loop and never occupies a worker thread. Database and HTTP checks in readiness are also `async def`, and they `await` their I/O.

### How do the lifespan and `Depends` compare with Nest lifecycle hooks and providers?

The lifespan is an async context manager passed to `FastAPI`. Code before `yield` runs once when the process starts, and the `finally` block runs once when it stops. That is the same role as Nest's `OnModuleInit` and `OnModuleDestroy`: create the engine and session factory at startup, and dispose the engine at shutdown.

`Depends` declares a request-scoped collaborator. FastAPI calls that function for the request and injects the return value. `ready` depends on `get_ready_status`, so the route receives a `ReadyStatus` instead of constructing clients itself. In Nest, a provider registered in a module plays the same role: the framework constructs it and injects it, and the handler does not look the dependency up by hand.

### What does one `AsyncSession` per request mean, and what does the empty Alembic revision change?

`get_session` opens one `AsyncSession` from the factory stored on `app.state`, yields it to the handler, and closes it when the request ends, including when the handler fails. The dependency does not commit. The handler owns the transaction and commits when its work is complete. A later request gets a new session. Nothing in Phase 1 calls `get_session` yet, because there are no database routes.

The revision `79d871cb4dad` has empty `upgrade` and `downgrade` functions. `alembic upgrade head` creates only Alembic's `alembic_version` table and stamps that revision. Running it again is a no-op. No research tables exist, so the schema can be designed when a feature needs it.

### How do pytest fixtures and dependency overrides stand in for a failed dependency?

A fixture sets up something a test needs and tears it down afterwards. The integration fixture reads the configured database URL. It recreates `nexus_test` only when the host is `localhost`, `127.0.0.1`, or `::1` and the URL names the development database. A URL that already names `nexus_test`, `postgres`, `template0`, or `template1` stops the fixture before any `DROP DATABASE`. `DROP DATABASE` cannot take a query parameter, so the fixture allowlists the name `nexus_test` before quoting it into that statement. It then creates that database, yields the URL, and drops it when the test finishes. The development database `nexus` is never the target of that SQL.

A unit test can replace a FastAPI dependency without stopping a container. `app.dependency_overrides[get_ready_status]` returns a `ReadyStatus` that says Postgres or Qdrant is unavailable. `GET /api/v1/health/ready` then returns 503, and the same app still returns 200 from `/live`. The override is the test double. Other unit tests call `postgres_is_ready` and `qdrant_is_ready` against a closed local port, so the real connection code fails without stopping Docker. The real containers stay up for the integration test that expects both checks to be `ok`.

## Technical debt

- Uvicorn does not read `NEXUS_HOST` or `NEXUS_PORT`. The CLI flags must match those settings. The defaults are both `127.0.0.1:8000`.
- Readiness opens its own engine. It invalidates and closes that connection before dispose, because dispose does not close a connection that is still checked out. The application pool is not used for the probe.
- Qdrant has a documented `GET /readyz` probe and no Compose health check.
- Integration tests require the local Compose services and `apps/api/.env`.
- The initial Alembic revision creates no application tables.

## What Phase 2 needs from this phase

Phase 2 starts from an API that still installs from the lockfile, migrates a clean database, and reports live and ready correctly. Phase 2 adds OpenAI, structured outputs, and a web-search tool. LangGraph and PDF retrieval stay later.
