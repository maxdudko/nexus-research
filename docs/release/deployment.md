# Deployment

**Status:** draft for `v0.1.0`. Quickstart, upgrade and backup sections are added with the release documentation.

The release stack is `compose.release.yml`: Web, API, PostgreSQL, Qdrant and a one-shot `init` service on one host. Only Web is published, on `127.0.0.1`. There is no authentication and no per-user isolation. `research_id` scopes evidence; it does not authorize anyone.

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
