# Operations

Day-to-day operation of the `v0.1.0` release stack. Setup is in [`deployment.md`](deployment.md). Commands run from the repository root and use the same Compose invocation:

```bash
alias nr='docker compose --env-file .env.release -f compose.release.yml'
```

The default user and database are `nexus`. If you changed `POSTGRES_USER` or `POSTGRES_DB`, substitute them in the `psql` and `pg_dump` commands.

## Health checks

| Check | Command | Healthy result |
|---|---|---|
| Container state | `nr ps -a` | `postgres`, `qdrant`, `api`, `web` are `healthy`; `init` is `Exited (0)` |
| Liveness (through Web) | `curl -fsS http://127.0.0.1:3000/api/v1/health/live` | 200 `{"status":"ok"}` |
| Readiness (through Web) | `curl -sS http://127.0.0.1:3000/api/v1/health/ready` | 200 `{"status":"ok","postgres":"ok","qdrant":"ok"}` |
| One container's probe history | `docker inspect -f '{{json .State.Health}}' "$(nr ps -q api)"` | recent `ExitCode` 0 entries |

- Liveness does no I/O. It stays 200 while PostgreSQL or Qdrant is down.
- Readiness returns 503 with `"status":"unavailable"` and marks the failing dependency `unavailable`. It does not include exception text or connection details. Each probe is bounded by `NEXUS_READINESS_TIMEOUT_SECONDS` (default 2).
- The `api` container health check calls readiness, so a data-service outage turns `api` unhealthy. Restart policies are `unless-stopped`; Compose does not restart a container just because it is unhealthy.
- The `web` health check fetches `/` from the production server.
- If `api` is stopped, Web stays up and its proxy returns 502 (for example `history_unavailable` on `/api/v1/research/runs`).

## Logs

```bash
nr logs -f api                    # follow the API
nr logs --since 1h api web        # recent API and Web output
nr logs init                      # last initialization
```

- With the default `NEXUS_LOG_FORMAT=json`, application logs from the `nexus` logger are one JSON object per line with `timestamp`, `level`, `logger`, `event` and allowlisted fields such as `run_id`, `research_id`, `stage`, `operation`, `outcome`, `error_code` and `duration_ms`. Uvicorn access lines are plain text: client address, method, path and status.
- Fields outside the allowlist are dropped. Prompts, excerpts, URLs, queries, request bodies and key-shaped strings (`sk-…`, `tvly-…`, `Bearer …`) are removed or replaced with `[redacted…]`. The packaged smoke test checks that the database password, provider keys and the database URL do not appear in any service log.
- Docker's default `json-file` log driver keeps container logs until the container is removed and does not rotate them. Configure rotation in the Docker daemon if the host runs for long periods.

### Cost per run

The UI does not show cost. Each research run emits one `research.usage` event:

```bash
nr logs api | grep '"event":"research.usage"'
```

It can contain `coverage`, `estimated_usd`, `pricing_version`, `currency`, `input_tokens`, `output_tokens`, `embedding_tokens`, `operation_count` and `unknown_operations`. `estimated_usd` is an estimate from the price table in pricing version `2026-10-19`, not an invoice. `coverage` is:

- `complete` only when every billable operation was measured and priced;
- `partial` when something is missing, which is the normal case with the default `NEXUS_AI_MAX_RETRIES=1` because SDK retries are not visible;
- `unavailable` when nothing could be priced.

Tavily searches are priced only when `NEXUS_TAVILY_USD_PER_SEARCH` is set. Check the provider dashboards for actual spend.

## Failed initialization

Symptoms: `nr up -d` reports that `init` did not complete successfully, `api` and `web` are not created or not running, and port 3000 does not answer.

```bash
nr ps -a
docker inspect -f '{{.State.ExitCode}}' "$(nr ps -a -q init)"
nr logs init
```

| What `init` logged | Likely cause | Fix |
|---|---|---|
| `Invalid configuration` and `NEXUS_DATABASE_URL: … development database password is not allowed in production` | `POSTGRES_PASSWORD=nexus` | Generate a new password. If the volume already exists, change it inside PostgreSQL first ([Changing the PostgreSQL password](deployment.md#changing-the-postgresql-password)). |
| An authentication error from Alembic or `checkpoint setup failed` | `POSTGRES_PASSWORD` changed after the volume was created | Restore the old value, or change the role password inside PostgreSQL to match. |
| No `init -` lines, and `postgres` is not healthy | PostgreSQL did not start | `nr logs postgres`. Check disk space and the volume. |
| `init - domain schema at head` but not `init - checkpoint tables ready` | The checkpoint setup failed after the domain migration succeeded | Read the error class in the log, fix the cause, then rerun. Both steps are idempotent. |

Rerun after fixing the cause:

```bash
nr run --rm init
nr up -d
```

Do not delete volumes to get past an init failure unless you intend to lose all data. Nothing in the stack downgrades a schema automatically.

## Persistence

| Volume | Contents |
|---|---|
| `nexus-release_postgres_data` | `users` and `auth_sessions`, `research_scopes` (one owner per research id), `research_runs` and `research_reports` (history and reports), `sources` (one row per indexed PDF or page, with its digest), `alembic_version`, and the LangGraph checkpoint tables |
| `nexus-release_qdrant_data` | The `nexus_evidence_v1` collection: chunk vectors and passage payloads, filtered by `research_id` |

`stop`, `down`, image rebuilds and host reboots keep both volumes. Only `down -v` or `docker volume rm` deletes them.

The two volumes describe one evidence state. Retrieval reads the accepted digest of each source from PostgreSQL and then searches Qdrant. Back up and restore them together, from the same moment. Restoring only one of them leaves sources without passages or passages without an accepted source.

Original PDF bytes are not stored anywhere. Lost PDF evidence can be rebuilt only by uploading the file again.

Cold-copy both volumes before an upgrade that adds ownership. `init` only runs `alembic upgrade head`. That head is two revisions: `c8e4a1b27d05` (accounts, sessions, and the quarantine user) and then `e3b7d91c4a20` (one scope per existing research id, with foreign keys). Pre-Alpha research ids become scopes owned by the non-loginable quarantine user `00000000-0000-4000-8000-000000000001`. They stay unreadable through the API until an operator adopts a reviewed id.

Each revision is its own transaction. A failure inside one rolls that revision back and leaves Qdrant and checkpoint bytes unchanged. A database left at `c8e4a1b27d05` has accounts but no scope foreign keys; the repair is another `upgrade head`, which only adds scopes. Do not start the previous image against either revision. Its Alembic scripts do not contain them, and `init` never downgrades.

Operator sequence after the new stack is healthy:

1. Keep the cold copy of both volumes, the recorded commit, and `.env.release` until the checks below succeed.
2. Let `init` upgrade to head. Do not name a revision.
3. Confirm legacy scopes are still quarantined: `select count(*) from research_scopes where owner_user_id = '00000000-0000-4000-8000-000000000001';`
4. Adopt one reviewed id, inside the API container: `python -m nexus.identity adopt-scope RESEARCH_ID EMAIL`. The command refuses a scope that already has a real owner.
5. Sign in as a different account and open that research id. The response is 404 and does not include the question.

The automated proof is the migration test that starts at `b7e2c91a4f10`, the v0.1.0 schema head, plus smoke's fresh init. That proof does not pull the `v0.1.0` image.

## Backups

The supported method is a **cold copy of both volumes while the stack is stopped**. It needs no extra image: the PostgreSQL image already pulled for the stack provides `tar`.

Keep backups outside the repository so they never enter a build context or a commit.

```bash
BACKUP_DIR="$HOME/nexus-backups/$(date -u +%Y%m%dT%H%M%SZ)"
PG_IMAGE='postgres:18.6@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336'
mkdir -p "$BACKUP_DIR"
git rev-parse HEAD > "$BACKUP_DIR/commit.txt"

nr stop
for volume in postgres_data qdrant_data; do
  docker run --rm --entrypoint tar \
    -v "nexus-release_${volume}:/source:ro" -v "$BACKUP_DIR:/backup" \
    "$PG_IMAGE" -C /source -czf "/backup/${volume}.tgz" .
done
nr start
```

- The archives are created by root inside the helper container and are owned by root on the host. Restrict them; they contain every question, report and indexed passage.
- `.env.release` is not part of this backup. Store the password separately: a restored PostgreSQL volume keeps the password it had when it was backed up.
- The default Qdrant snapshot directory (`/qdrant/snapshots`) is not on the `qdrant_data` volume, which mounts only `/qdrant/storage`. A Qdrant snapshot taken through its API is lost when the container is recreated unless you copy it out. The cold copy avoids that.

An optional logical dump of PostgreSQL can be taken while the stack runs:

```bash
nr exec -T postgres pg_dump -U nexus -d nexus -Fc > "$BACKUP_DIR/nexus.dump"
```

A dump alone is not a complete backup: Qdrant evidence is not in it, and a running stack can change between the dump and any Qdrant copy.

## Restoration

> **Not exercised by CI.** These steps follow from the volume layout above but no automated test restores a backup. Rehearse them on a non-production host and confirm the result before relying on them.

```bash
BACKUP_DIR="$HOME/nexus-backups/<timestamp>"
PG_IMAGE='postgres:18.6@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336'

nr down
for volume in postgres_data qdrant_data; do
  docker volume rm "nexus-release_${volume}"
  docker volume create "nexus-release_${volume}"
  docker run --rm --entrypoint tar \
    -v "nexus-release_${volume}:/target" -v "$BACKUP_DIR:/backup:ro" \
    "$PG_IMAGE" -C /target -xzf "/backup/${volume}.tgz"
done
git checkout "$(cat "$BACKUP_DIR/commit.txt")"
nr up -d --build
nr logs init
```

- Set `POSTGRES_PASSWORD` in `.env.release` to the password the backed-up database had.
- `docker volume create` without Compose labels makes Compose warn that the volume was not created by this project. The warning is harmless; the data is used.
- After `up`, check readiness, open a known run in the history, and run one evidence search for a known `research_id` (that search calls the OpenAI embedding API).

To restore only the logical dump into an initialized database: `nr exec -T postgres pg_restore -U nexus -d nexus --clean --if-exists < "$BACKUP_DIR/nexus.dump"`. Restore Qdrant from the same moment as well.

## Rollback limitations

The supported recovery after a real owner exists, or after invited users have written data, is the cold restore in [Restoration](#restoration). Stop the stack, restore `postgres_data` and `qdrant_data` from the same backup, check out the commit recorded with that backup, and point `.env.release` at the password that volume was using. Restoring only one volume is not supported. Writes after the backup are lost. Do not run the previous image against the new schema.

- `v0.1.0` is the first release. There is no earlier release stack to roll back to.
- For later releases, rolling back the images alone (checking out the previous tag, setting the previous `NEXUS_VERSION`, `up -d --build`) works only when the database schema is still compatible with the older code. `init` runs `alembic upgrade head` for whatever code it was built from; it never downgrades.
- If an upgrade ran a migration that the old code cannot use, the only supported rollback is to restore the backup taken before the upgrade, which discards everything written since.
- Running `alembic downgrade` by hand is not a supported release procedure. Checkpoint tables and Qdrant are not covered by Alembic. Downgrade order, if someone runs it anyway, is `e3b7d91c4a20` and then `c8e4a1b27d05`. The scope revision aborts while any owner is not the quarantine user and does not delete runs, reports, or sources. The identity revision then drops `auth_sessions` and `users`, and it aborts if `research_scopes` still exists. After a real owner exists, use the paired volume restore instead.
- There is no rollback guarantee for data written by a newer version.

## Stale `running` rows

Research is request-scoped. A run is executed only while its HTTP request is open. There is no worker, queue or resume.

| Event | Stored status |
|---|---|
| The user presses Stop or closes the tab | `interrupted`, written within `NEXUS_STREAM_CANCEL_PERSIST_SECONDS` (default 2) |
| That status write times out or fails | stays `running` |
| The API container is stopped or recreated during a run | `interrupted` if the cancellation write finishes inside Uvicorn's 10-second graceful shutdown, otherwise `running` |
| The process is killed, or the host loses power | stays `running` |

A `running` row whose request has ended is abandoned. The history API returns it as stored, and the UI says execution is not confirmed. Its checkpoint rows remain but are never resumed.

To list them (read-only):

```bash
nr exec -T postgres psql -U nexus -d nexus -c \
  "select run_id, created_at, started_at from research_runs where status = 'running' order by created_at"
```

Any row listed while no research request is in flight is stale. The release provides no cleanup command and no supported way to change its status. Start a new run with the same question if you need a result.

## Accounts, quotas, and residual risk

Research, evidence, and history require a session. Accounts are created with `python -m nexus.identity` (`bootstrap-admin`, `create-user`, `disable-user`, `reset-password`, `adopt-scope`). There is no HTTP admin API. An administrator does not receive another user's research.

A session lasts 12 hours from creation and goes idle after 4 hours. `nexus_session` is HttpOnly. `nexus_csrf` is the MAC of that session token, not a separate random value. Unsafe requests also send `Origin` and `X-CSRF-Token`. This stack does not terminate TLS. Production cookies are Secure unless `NEXUS_COOKIE_INSECURE_LOOPBACK` is set for the loopback bind.

Limits are memory inside the one API process. A restart clears them. Login throttling counts the address on the API socket and ignores `X-Forwarded-For` and `Forwarded`. Through the Next.js proxy that socket address is the web container, so the login address bucket is an instance-wide cap on failed sign-ins. The proxy does not send a verified client address. Trusting a forwarding header would let a caller choose a fresh bucket.

Each account may hold one `/run` and one `/stream`. Plan, discover, run, stream, PDF upload, web ingestion, and search share 10 requests per minute. Minting a scope is 30 per hour. Excess is HTTP 429 `too_many_requests` with `Retry-After`, except the process-wide stream cap, which keeps its own message. That cap is still `NEXUS_STREAM_MAX_CONCURRENT`.

Logout, password reset, or a disabled account ends a live stream at the next heartbeat. A provider call already inside the current node can finish and spend its cost. The successful history write locks the account and the session and stores `interrupted` with no report when that session is no longer valid. `/run` has no history row; the next provider node is its check. The remaining window for a success write is that one locked transaction.

These controls are not approval for a public deployment and do not tag `v0.2.0`.

## Provider and runtime failures

| HTTP | Code | Meaning |
|---|---|---|
| 503 | `provider_not_configured` | `NEXUS_OPENAI_API_KEY` is empty, or `NEXUS_TAVILY_API_KEY` is empty for a route that searches |
| 503 | `provider_unavailable` | Invalid key, connection failure or another provider error. Qdrant failures during ingestion also map here |
| 429 | `provider_rate_limited` | OpenAI or Tavily rate limit. Tavily 429 is not retried |
| 429 | `too_many_requests` | A process-local budget is full, or `NEXUS_STREAM_MAX_CONCURRENT` (default 2) streams are already running. Window and per-user slot responses include `Retry-After`. The process cap keeps the message "Too many research runs are in progress." |
| 504 | `provider_timeout` | A provider call or the discovery deadline timed out |
| 503 | `checkpoint_unavailable` | PostgreSQL is down or checkpoint tables are missing; rerun `init` |
| 503 | `history_unavailable` | The run history could not be read or written |
| 413 | `payload_too_large` | PDF over `NEXUS_PDF_MAX_BYTES` / `NEXUS_PDF_MAX_PAGES`, or an oversized web page |
| 415 / 422 | `unsupported_media` / `empty_document` | Not a PDF, an unreadable or encrypted PDF, or a document with no extractable text (for example a scan) |
| 422 | `unsafe_url` | A private, loopback, link-local or metadata address |

The HTTP status applies when the error happens before a response starts. Once `POST /api/v1/research/stream` has sent its headers, a failure arrives as one terminal `research.failed` event carrying the same `code`, and the run is stored as `failed` with that `error_code`. A deadline or cancellation ends the stream with `research.interrupted`.

A run that reaches its budget or deadline can still finish with `partial` or `insufficient_evidence`. Those are reports, not errors: the warnings list says which cap was hit or why the evidence was not enough. A `failed` run stores an `error_code` and has no report.

The complete list of injected failures and their expected outcomes is in [`docs/evaluation/failure-matrix.md`](../evaluation/failure-matrix.md).
