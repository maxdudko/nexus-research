# Demo

Two different things verify the release, and they prove different things:

- **Part A, the mocked smoke test**, runs in CI on every push with no provider keys. It proves the packaged stack starts, is private, streams and stops cleanly. It does not produce a report.
- **Part B, the manual paid-provider demo**, is run by a maintainer with real OpenAI and Tavily keys. It is the only evidence that research actually works end to end. It is never run in CI.

Passing Part A is not evidence that Part B would pass. Do not describe the release as demonstrated until Part B has been run and its outcomes recorded in [`checklist.md`](checklist.md).

## Part A: mocked CI smoke

```bash
scripts/release-smoke.sh              # builds both images first
scripts/release-smoke.sh --no-build   # reuses nexus-api:0.1.0 and nexus-web:0.1.0
```

The script starts an isolated Compose project (`nexus-smoke-<pid>`) with throwaway volumes, a random loopback port, a random database password and fake provider keys. `compose.smoke.yml` adds a `stub-openai` container and points the OpenAI SDK at it. The stub counts requests and never answers, so a stream stays open without spending credit. Tavily is never reached. Everything is deleted on exit. On failure, scrubbed logs go to `$SMOKE_ARTIFACTS` (default `/tmp/nexus-smoke`).

It checks, in order:

1. `compose.release.yml` and the smoke override render, and `scripts/check_release_compose.py` passes.
2. Both images run as a non-root user, contain no `.env` file and no secret-like environment variable, and the browser bundle references no server setting.
3. `init` exits 0 after both `init -` lines, and `api` and `web` become healthy.
4. Through Web: `/` renders HTML; live is 200; ready is 200 with both dependencies `ok`; anonymous run history is 401 `unauthenticated`. Inside the API container `/docs` and `/openapi.json` are 404. No provider request happened during startup.
5. Only `web` is published, on `127.0.0.1`.
6. Rerunning `init` exits 0 and leaves `alembic_version` and `checkpoint_migrations` unchanged.
7. After `init`, `bootstrap-admin` creates `smoke@example.com` inside the API container. Anonymous `POST /api/v1/research/stream` is 401 `unauthenticated` and does not reach the model stub. Login through Web sends `Origin: http://127.0.0.1:<port>`, which matches `NEXUS_TRUSTED_ORIGINS`, and the script keeps the cookie jar plus `X-CSRF-Token`. Authenticated run history is 200. An authenticated stream starts with `research.started`, delivers at least three heartbeats in six seconds, and reaches the model stub. Closing the client stores the run as `interrupted`.
8. Stopping the API during an authenticated stream finishes within the grace period without a kill. Web keeps serving. An anonymous history request returns 502 `history_unavailable`, because the proxy fails before the API. The run is stored as `interrupted`, or left `running` with a warning (the documented stale-running case).
9. Stopping PostgreSQL, then Qdrant, makes ready 503 while live stays 200, and ready recovers.
10. After `down` and `up`, both runs are still in the authenticated history.
11. No service log contains the database password, the smoke account password, either provider key, or the database URL.

It does **not** show:

- that OpenAI or Tavily accept the configured keys or model;
- any planning, search, page fetch, embedding, Qdrant ingestion or synthesis result;
- a completed, partial or insufficient report, or any citation;
- that checkpoint rows or Qdrant evidence survive a restart (step 10 checks run history only);
- latency, cost or quality of real research.

## Part B: manual paid-provider demo

This spends real OpenAI and Tavily credit. Run it only with approved spend, on the release stack described in [`deployment.md`](deployment.md), with both keys set in `.env.release`. Keep the default budgets unless you record the change.

Create the operator account once. The password is read from stdin and is not an argument:

```bash
printf '%s\n' 'a-long-password' | docker compose --env-file .env.release -f compose.release.yml exec -T api \
  python -m nexus.identity bootstrap-admin you@example.com
```

Sign in at `http://127.0.0.1:3000/login` before the scenarios. Later curls need that session: send the `nexus_session` cookie, and on POST also `Origin: http://127.0.0.1:3000` and `X-CSRF-Token` equal to the `nexus_csrf` cookie. Anonymous research is 401.

Before you start, record the commit (`git rev-parse HEAD`), the date, `NEXUS_OPENAI_MODEL` (default `gpt-4o-mini`) and any non-default tunables. Keep a second terminal on the API logs:

```bash
docker compose --env-file .env.release -f compose.release.yml logs -f api | grep '"event":"research.usage"'
```

### Scenario 1: web research

1. Open `http://127.0.0.1:3000` without attaching a PDF.
2. Ask: `How do AI coding agents improve software development workflows?` (any question of 10–2000 characters works; record the one you used).
3. While it runs, the status reads "Researching", the elapsed time and last heartbeat update, and stage entries appear from `planning`, `discovery`, `ingestion`, `retrieval`, `evaluation` and `synthesis` as the graph reaches them. A stage can repeat on a second iteration.
4. When the stream ends, the UI opens `/research/<run_id>`. The status is one of:
   - `completed`: a report whose sections cite retrieved evidence;
   - `partial`: a report produced after a budget or deadline was hit, or after synthesis fell back; its warnings say why;
   - `insufficient_evidence`: the run did not find enough evidence; the report says so rather than answering;
   - `failed`: no report, and an error code.

   All four are legitimate outcomes. Record the one you got; do not rerun until a "better" one appears without recording each attempt.
5. Follow at least one `https` citation and check that it opens the source page in a new tab. A citation means the passage was retrieved for this run. It is not proof that the passage supports the sentence; judge that yourself and note it.
6. Refresh the page. The same report loads again from PostgreSQL.
7. Open the history list on `/` and check that the run appears with its status.
8. Copy the `research.usage` line for this run from the log terminal. Record `coverage` and `estimated_usd` exactly as printed. With default settings expect `coverage` to be `partial`, and Tavily cost absent unless `NEXUS_TAVILY_USD_PER_SEARCH` is set.

### Scenario 2: text PDF (optional)

Use a PDF you are allowed to upload: text-based (not scanned), up to 10 MiB and 100 pages. The repository does not ship one. Record its file name, page count and where it came from.

1. On `/`, attach the PDF and ask a question that the PDF can answer.
2. The status first reads "Indexing PDF" while the API extracts, chunks and embeds it, then "Researching".
3. In the finished report, PDF citations appear as `PDF · page N` labels. They are not links: the original file is not stored, so there is nothing to open.
4. Check one cited page number against your copy of the PDF.
5. Expected rejections, if you want to show them: a scanned PDF is rejected with no extractable text; a file over the caps returns `payload_too_large`; a non-PDF returns `unsupported_media`.

### Scenario 3: controlled cancellation

1. Start a new question.
2. Once at least one stage has appeared, press Stop ("Stop this request; research may be interrupted.").
3. The status becomes "Interrupted". Open the run from the history: it is stored as `interrupted`. In the rare case that the status write timed out it stays `running`; record that as observed.
4. Nothing resumes the run later.

### Persistence after restart (AC-05)

After Scenario 1 (and 2 if run), restart the stack and check that history, checkpoints and evidence remain. Replace `<run_id>` with the run from Scenario 1.

```bash
alias nr='docker compose --env-file .env.release -f compose.release.yml'
nr exec -T postgres psql -U nexus -d nexus -Atc "select count(*) from checkpoints"
nr down && nr up -d
nr exec -T postgres psql -U nexus -d nexus -Atc "select count(*) from checkpoints"
curl -fsS http://127.0.0.1:3000/api/v1/research/runs/<run_id>
```

The checkpoint count is the same before and after, and the run detail still returns its report. Then search the evidence of that run (this makes one embedding call):

```bash
RESEARCH_ID="$(curl -fsS http://127.0.0.1:3000/api/v1/research/runs/<run_id> \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["run"]["research_id"])')"
curl -fsS -X POST http://127.0.0.1:3000/api/v1/evidence/search \
  -H 'content-type: application/json' \
  -d "{\"research_id\":\"$RESEARCH_ID\",\"query\":\"AI coding agents\",\"top_k\":5}"
```

A non-empty `matches` list shows Qdrant evidence survived the restart. An empty list after a run that cited web pages is a failure to record.

## Evidence record

Copy this block into [`checklist.md`](checklist.md) under AC-10, one per attempt. Fill in only what you observed. Leave a field as `not recorded` rather than estimating it.

```text
Date (UTC):
Commit:
Operator:
Model (NEXUS_OPENAI_MODEL):
Non-default settings:

Scenario 1 — web research
  Question:
  run_id:
  Final status:
  Stages observed:
  Number of citations / sources:
  Citation checked (URL) and judgement:
  Refresh and history reload OK (yes/no):
  research.usage coverage / estimated_usd:
  Notes:

Scenario 2 — text PDF (or "not run")
  PDF (name, pages, origin):
  run_id:
  Final status:
  Page label checked against the file:
  Notes:

Scenario 3 — cancellation
  run_id:
  Stored status:
  Notes:

Persistence after restart
  checkpoints before / after:
  Run detail after restart OK (yes/no):
  Evidence search matches after restart:
```
