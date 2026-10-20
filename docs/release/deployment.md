# Deployment

This guide covers `v0.1.0`. The release stack is `compose.release.yml`: Web, API, PostgreSQL, Qdrant and a one-shot `init` service on one host, in the Compose project `nexus-release`. Only Web is published, on `127.0.0.1`. There is no authentication and no per-user isolation. `research_id` scopes evidence; it does not authorize anyone.

Every command below runs from the repository root. To shorten them:

```bash
alias nr='docker compose --env-file .env.release -f compose.release.yml'
```

The alias is only a convenience; the full form is shown where it matters. Day-to-day operation is in [`operations.md`](operations.md), and the known limits are in [`limitations.md`](limitations.md).

## Prerequisites

- Docker Engine with the Docker Compose plugin, v2 or later (`docker compose version`). The stack uses `depends_on` conditions, including `service_completed_successfully`.
- linux/amd64. Other architectures have not been tested.
- `openssl` to generate the database password, `curl` for the health checks, and `python3` for `scripts/check_release_compose.py`.
- An OpenAI API key and a Tavily API key if you want real research. Both accounts are billed per run.
- Network egress from the host to Docker Hub and `ghcr.io` for the first build, and to the OpenAI and Tavily APIs at runtime.

Local Python, uv and Node are not needed; both images are built inside Docker from `apps/api/uv.lock` and `apps/web/package-lock.json`.

## Environment setup

```bash
cp .env.release.example .env.release
chmod 600 .env.release
openssl rand -hex 32   # paste the output into POSTGRES_PASSWORD
```

Then edit `.env.release`. The file has two kinds of variables:

| Kind | Variables | Who reads them |
|---|---|---|
| Compose interpolation | `POSTGRES_PASSWORD` (required), `POSTGRES_USER`, `POSTGRES_DB`, `NEXUS_WEB_PORT`, `NEXUS_VERSION` | Docker Compose, while rendering `compose.release.yml`. They configure the PostgreSQL container, build `NEXUS_DATABASE_URL`, choose the host port and tag the images. |
| API runtime | `NEXUS_OPENAI_API_KEY`, `NEXUS_TAVILY_API_KEY`, the optional `NEXUS_*` tunables, `NEXUS_OTEL_*`, `NEXUS_LOG_FORMAT`, `NEXUS_TAVILY_USD_PER_SEARCH` | The API container only. Each name is listed explicitly in the `api` service; a name that is not listed there never reaches the container. |

`compose.release.yml` sets `NEXUS_ENVIRONMENT=production`, `NEXUS_DATABASE_URL` and `NEXUS_QDRANT_URL=http://qdrant:6333` itself. Do not put them in `.env.release`. Web receives only `API_PROXY_TARGET=http://api:8000`. No variable uses the `NEXT_PUBLIC_` prefix, so nothing from this file is compiled into the browser bundle.

Rules that the stack enforces:

- A missing `POSTGRES_PASSWORD` stops `docker compose config` and `up` with `set POSTGRES_PASSWORD in .env.release`.
- In production the API refuses the development password `nexus`. `init` exits 1 and the API does not start.
- Use URL-safe characters for the password. It is embedded in `NEXUS_DATABASE_URL`. `openssl rand -hex 32` is safe.
- A tunable left commented keeps the API default. An empty value (`NEXUS_GRAPH_DEADLINE_SECONDS=`) is invalid and stops the API with the variable name. The keys, `NEXUS_OTEL_ENDPOINT` and `NEXUS_TAVILY_USD_PER_SEARCH` are the exceptions: empty means unset.
- Without provider keys the stack starts and is healthy, and research routes return 503 `provider_not_configured`.

`NEXUS_RAG_COLLECTION` and the chunking settings are not passed to the API container, so the release stack always uses the defaults (`nexus_evidence_v1`, 800-token chunks with 120 overlap).

## Startup

```bash
docker compose --env-file .env.release -f compose.release.yml config --quiet
python3 scripts/check_release_compose.py
docker compose --env-file .env.release -f compose.release.yml up -d --build
docker compose --env-file .env.release -f compose.release.yml ps -a
```

- `config --quiet` renders the file and fails on a missing required variable.
- `check_release_compose.py` renders the template with a throwaway password and sentinel keys. It checks published ports, networks and which service receives which variable. It does not read `.env.release`.
- `up -d --build` builds `nexus-api:${NEXUS_VERSION}` and `nexus-web:${NEXUS_VERSION}` and starts the services in this order: `postgres` and `qdrant` until healthy, `init` until it exits 0, `api` until `/api/v1/health/ready` returns 200, then `web`.
- In `ps -a`, `init` shows `Exited (0)`, and `postgres`, `qdrant`, `api` and `web` show `healthy`.

Verify through the published port:

```bash
curl -fsS http://127.0.0.1:3000/api/v1/health/live
curl -fsS http://127.0.0.1:3000/api/v1/health/ready
```

The first returns `{"status":"ok"}`, the second `{"status":"ok","postgres":"ok","qdrant":"ok"}`. Then open `http://127.0.0.1:3000`. If you changed `NEXUS_WEB_PORT`, use that port.

In production the API serves neither `/docs` nor `/openapi.json`.

## Schema initialization

PostgreSQL holds two schemas with different owners:

- **Domain schema**, owned by Alembic: `sources`, `research_runs`, `research_reports` and `alembic_version`.
- **LangGraph checkpoint tables**, created by `python -m nexus.research.checkpoint`. Alembic does not manage them.

The `init` service is built from the API image and runs both, in order, on every `up`:

```text
alembic upgrade head
python -m nexus.research.checkpoint
```

Both commands are idempotent. A successful run logs `init - domain schema at head` and then `init - checkpoint tables ready`:

```bash
docker compose --env-file .env.release -f compose.release.yml logs init
```

`init` uses `set -eu`: if either command fails, it exits non-zero, the API is not started, and Web is not started either. Nothing falls back to an uninitialized schema, and nothing downgrades or resets a volume. Recovery is in [Failed initialization](operations.md#failed-initialization).

To rerun initialization by hand against the running database:

```bash
docker compose --env-file .env.release -f compose.release.yml run --rm init
```

## Network isolation

| Service | Networks | Host port |
|---|---|---|
| `postgres` | `data` | none |
| `qdrant` | `data` | none |
| `init` | `data` | none |
| `api` | `data`, `edge` | none (`expose: 8000` is container-only) |
| `web` | `edge` | `127.0.0.1:${NEXUS_WEB_PORT:-3000}` |
| `jaeger` (opt-in) | `edge` | `127.0.0.1:16686` |

- `data` is `internal: true`. PostgreSQL, Qdrant and `init` have no egress and cannot be reached from the host.
- `edge` serves Web and gives the API egress to OpenAI and Tavily.
- The host address of the Web port is fixed to `127.0.0.1` in the file. Only the port number is configurable.
- Services reach each other by Compose DNS (`postgres`, `qdrant`, `api`), never by `localhost`.

To check the running stack, `ps` must show exactly one published port, on `127.0.0.1`:

```bash
docker compose --env-file .env.release -f compose.release.yml ps --format '{{.Service}} {{.Ports}}'
```

### Optional tracing

Jaeger runs only with the `observability` profile. Set `NEXUS_OTEL_ENABLED=true` and `NEXUS_OTEL_ENDPOINT=http://jaeger:4318/v1/traces` in `.env.release`, then:

```bash
docker compose --env-file .env.release -f compose.release.yml --profile observability up -d
```

The Jaeger UI is `http://127.0.0.1:16686`. Traces stay in Jaeger memory. An exporter failure does not change a research result.

## Private remote access

Do not publish port 3000 on a public interface. Every research request spends the operator's OpenAI and Tavily credit, and the history API lists every run.

To use the stack from another machine, keep the loopback binding and use one of:

- **SSH tunnel.** From the client, run `ssh -N -L 3000:127.0.0.1:3000 user@release-host`, then open `http://127.0.0.1:3000`.
- **VPN.** Reach the host over a private network and tunnel to loopback as above. Do not change the published address in `compose.release.yml`.
- **An independently enforced authentication gate.** For example, a reverse proxy that requires single sign-on or client certificates before forwarding to `127.0.0.1:3000`. The gate must authenticate every request, including `/api/v1/*`.

HTTPS on its own is not access control. A TLS reverse proxy without authentication still exposes billable endpoints to anyone who finds the address.

## Timeouts

The UI uses `POST /api/v1/research/stream`. Headers are sent immediately and a `: ping` comment follows every `NEXUS_STREAM_HEARTBEAT_SECONDS` (15 by default), so the stream stays active for the whole run.

- **A reverse proxy in front of Web** needs a read timeout above `NEXUS_GRAPH_DEADLINE_SECONDS` plus 5 seconds; with the default 180, 200 is safe. Disable response buffering for `text/event-stream`. The API already sends `X-Accel-Buffering: no` and `Cache-Control: no-cache, no-transform`.
- **The Web proxy itself** uses Node `fetch`, which waits at most 300 seconds for response headers and 300 seconds between body chunks. Heartbeats keep the stream inside both limits.
- **`POST /api/v1/research/run`** returns headers only after the graph finishes. Through the Web proxy it fails once a run exceeds about 290 seconds. Keep `NEXUS_GRAPH_DEADLINE_SECONDS` at 280 or lower if you call `/run` through port 3000. The UI does not use `/run`.

## Secrets

`.env.release` holds the PostgreSQL password and the provider keys. Docker Compose reads it and passes each value only to the service that needs it: provider keys reach the API container only, and Web receives nothing from it. It is a plain file, not a secret manager. Values are visible to anyone who can run `docker inspect` on the host.

- Keep the file out of version control (`.gitignore` covers it) and restrict it: `chmod 600 .env.release`.
- The API refuses to start in production with the development database password `nexus`.
- Standard logs do not include provider keys, the database URL or the database password. Uvicorn access lines contain client address, method, path and status only. Research questions travel in request bodies, not URLs.
- The images contain no `.env` file and no secret-like environment variable; `scripts/release-smoke.sh` checks both.

### Rotating a provider key

1. Edit `NEXUS_OPENAI_API_KEY` or `NEXUS_TAVILY_API_KEY` in `.env.release`.
2. Run `docker compose --env-file .env.release -f compose.release.yml up -d`. Compose recreates the API container with the new value.
3. A research run in progress during the restart is stopped and not resumed. Uvicorn waits 10 seconds for open streams and then cancels them, so the run can stay `running` in history instead of `interrupted`. Start a new run.
4. Revoke the old key with the provider.

### Changing the PostgreSQL password

`POSTGRES_PASSWORD` is applied only when the database volume is first initialized. Changing it in `.env.release` alone makes `init` fail with an authentication error, and the API does not start.

1. Change the role password inside the running database. The interactive prompt keeps the value out of shell history:

   ```bash
   docker compose --env-file .env.release -f compose.release.yml exec postgres psql -U nexus -d nexus
   # then: \password nexus
   ```

2. Put the same value in `POSTGRES_PASSWORD` in `.env.release`. Use URL-safe characters (`openssl rand -hex 32`).
3. Run `docker compose --env-file .env.release -f compose.release.yml up -d`. `init` and the API reconnect with the new password.

If you changed `POSTGRES_USER` or `POSTGRES_DB`, use those names in place of `nexus` in the `psql` commands throughout these guides.

## Upgrades

An upgrade replaces the images and reruns `init` against the existing volumes. It is not zero-downtime: in-flight research is stopped and not resumed.

1. Let running research finish, or accept that it will be interrupted.
2. Take a backup as described in [Backups](operations.md#backups). Restoring that backup is the only rollback path if a migration cannot be undone.
3. Read [`CHANGELOG.md`](../../CHANGELOG.md) for the target version, including new or renamed variables in `.env.release.example`.
4. Fetch the reviewed revision, for example `git fetch --tags && git checkout v0.1.0`.
5. Set `NEXUS_VERSION` in `.env.release` to the new version so the old images keep their tag.
6. Rebuild and restart:

   ```bash
   docker compose --env-file .env.release -f compose.release.yml config --quiet
   docker compose --env-file .env.release -f compose.release.yml up -d --build
   docker compose --env-file .env.release -f compose.release.yml logs init
   curl -fsS http://127.0.0.1:3000/api/v1/health/ready
   ```

7. Confirm both `init -` lines are present and readiness is 200. If `init` failed, stop here and follow [Failed initialization](operations.md#failed-initialization).

`v0.1.0` is the first release, so there is no earlier release stack to upgrade from. A development database from `docker-compose.yml` is a different project with different volumes and is not migrated into the release stack.

## Shutdown

```bash
docker compose --env-file .env.release -f compose.release.yml stop   # stop containers, keep them and the volumes
docker compose --env-file .env.release -f compose.release.yml down   # remove containers and network, keep the volumes
```

The API and Web each have a 15-second stop grace period. Uvicorn waits up to 10 seconds for open streams. A run cut off by shutdown is not resumed; it is stored as `interrupted` or stays `running` (see [Stale `running` rows](operations.md#stale-running-rows)).

The next `up -d` reuses the volumes `nexus-release_postgres_data` and `nexus-release_qdrant_data`, so history, checkpoints and evidence remain.

```bash
docker compose --env-file .env.release -f compose.release.yml down -v   # deletes all release data
```

`down -v` deletes both volumes: run history, reports, checkpoints and every indexed passage. Original PDFs were never stored and cannot be recovered from anywhere.
