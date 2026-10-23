# Release checklist: `v0.1.0` (Gate C)

This checklist decides whether `v0.1.0` can be tagged. Acceptance criteria come from [`docs/specs/phase-7-release/spec.md`](../specs/phase-7-release/spec.md) section 7; the decision rules come from the Gate C section of [`plan.md`](../specs/phase-7-release/plan.md).

Rules for filling it in:

- Record only evidence that was actually observed: a CI link, a command and its output, or a filled demo record. Never write expected results into an evidence field.
- Every item must pass on the **release commit**, the exact SHA that will be tagged. Evidence from an earlier commit is a baseline, not a pass.
- Tagging and publishing are manual maintainer actions. Nothing in CI tags, pushes or publishes a release. Pushing a tag runs the Release workflow. It verifies the tag and keeps the built images as a workflow artifact only.

## Release record

| Field | Value |
|---|---|
| Release commit (SHA) | Documentation commit on top of `7096289ac8ee4b9ecf49e7002f088a8ba8013d35`. The commands below were run on that parent. This commit changes docs only; tag it after CI on it is green. |
| Branch | `development` |
| Date (UTC) | 2026-10-20 |
| CI run on the release commit | Code parent: [run 39450946288](https://github.com/maxdudko/nexus-research/actions/runs/39450946288) (`success`; API, Web, Release images and smoke). The run for this docs commit is linked from the GitHub release. |
| Offline baseline | `docs/evaluation/baseline.json`, dataset `2026-10-19.2`, report version `1` |
| Images | `nexus-api:0.1.0`, `nexus-web:0.1.0`, built locally from the release commit (not published to a registry) |
| Tested platform | linux/amd64 |
| Reviewer | maxdudko |

### Baseline evidence (before the documentation commit)

[CI run 39438258990](https://github.com/maxdudko/nexus-research/actions/runs/39438258990) on `development` at `d6fb04dffc010b98e2f88f725b248305ee9094e4`: jobs **API**, **Web** and **Release images and smoke** all concluded `success` (checked through the GitHub REST API on 2026-10-20). That covers AC-01, AC-02, AC-03, AC-04 (infrastructure part), AC-06, AC-07 and AC-08 at that commit. The documentation commit changes no code, but it is a new commit: CI must be green on it too.

## Acceptance criteria

Run the commands from the repository root. `nr` is `docker compose --env-file .env.release -f compose.release.yml`.

### AC-01: clean image builds (blocking)

**Criterion:** a fresh clone builds both images without local Python or Node.

```bash
git clone https://github.com/maxdudko/nexus-research.git /tmp/nexus-rc && cd /tmp/nexus-rc
git checkout <release-sha>
docker build -t nexus-api:0.1.0 apps/api
docker build -t nexus-web:0.1.0 apps/web
```

**Pass:** both builds exit 0, and the CI job "Release images and smoke" is green on the release commit.
**Evidence:** Fresh clone `/tmp/nexus-rc` at `7096289ac8ee4b9ecf49e7002f088a8ba8013d35`. `docker build -t nexus-api:0.1.0 apps/api` and `docker build -t nexus-web:0.1.0 apps/web` both exited 0 (local layer cache was used). CI job "Release images and smoke" succeeded in [run 39450946288](https://github.com/maxdudko/nexus-research/actions/runs/39450946288). **Status:** [x]

### AC-02: full-stack startup (blocking)

**Criterion:** one documented Compose invocation starts the stack after configuration.

```bash
cp .env.release.example .env.release && chmod 600 .env.release   # set POSTGRES_PASSWORD
nr config --quiet
nr up -d --build
nr ps -a
```

**Pass:** `config` exits 0; `postgres`, `qdrant`, `api` and `web` are `healthy`; `init` is `Exited (0)`.
**Evidence:** In `/tmp/nexus-rc`, `config --quiet` exited 0. The first `up -d --build` reused a stopped local `nexus-release` stack: `api` was not recreated and stayed unhealthy (`qdrant readiness check failed: ConnectError`). `down -v` and a fresh `up -d --build` exited 0. `ps -a` then showed `postgres`, `qdrant`, `api` and `web` healthy, and `init` `Exited (0)`. **Status:** [x]

### AC-03: migrations and checkpoint setup, repeated (blocking)

**Criterion:** domain migrations and checkpoint setup run on a fresh database and can be repeated.

```bash
nr logs init | grep -E 'init - (domain schema at head|checkpoint tables ready)'
nr run --rm init
nr exec -T postgres psql -U nexus -d nexus -Atc "select version_num from alembic_version"
```

**Pass:** both `init -` lines appear on the fresh stack; the rerun exits 0; the Alembic head is unchanged. `release-smoke.sh` step 6 checks the same thing automatically.
**Evidence:** Fresh init logs contained `init - domain schema at head` and `init - checkpoint tables ready`. `run --rm init` exited 0 and printed both lines again. With this machine's `POSTGRES_USER=admin`, `psql -d nexus -Atc "select version_num from alembic_version"` returned `b7e2c91a4f10`. Smoke step 6: `init rerun exits 0 and leaves schema unchanged (b7e2c91a4f10|10:9)`. **Status:** [x]

### AC-04: API, Web, proxy and SSE (blocking)

**Criterion:** the API is ready, Web and its proxy are reachable, and SSE is not buffered.

```bash
curl -fsS http://127.0.0.1:3000/api/v1/health/live
curl -fsS http://127.0.0.1:3000/api/v1/health/ready
scripts/release-smoke.sh
```

**Pass:** live returns `{"status":"ok"}`; ready returns `{"status":"ok","postgres":"ok","qdrant":"ok"}`; the smoke prints `all release smoke checks passed`, which includes heartbeats arriving through Web before the stream ends (step 7). Progress events from a real run are confirmed under AC-10.
**Evidence:** `curl` through `http://127.0.0.1:3000` returned `{"status":"ok"}` and `{"status":"ok","postgres":"ok","qdrant":"ok"}`. `scripts/release-smoke.sh --no-build` in `/tmp/nexus-rc` exited 0 and printed `all release smoke checks passed`. Step 7: `research.started` then 5 heartbeats before the client closed. The live UI run in AC-10 showed `Status: Researching`, an updating heartbeat, and stage rows. **Status:** [x]

### AC-05: persistence across restart (blocking)

**Criterion:** a restart keeps history, checkpoints and Qdrant evidence.

- History: `release-smoke.sh` step 10 (automatic).
- Checkpoints and evidence: **not covered by the smoke test.** Run the "Persistence after restart" steps in [`demo.md`](demo.md#persistence-after-restart-ac-05) after a real run. The evidence search needs an OpenAI key.

**Pass:** smoke step 10 passes; the `checkpoints` count is unchanged across `down` / `up`; the run detail still returns its report; the evidence search for that run's `research_id` returns at least one match.
**Evidence:** Smoke step 10 passed (`run history survives down and up`). After scenario 1, `select count(*) from checkpoints` was `35` before `down` and `35` after `up -d`. Run `1d15319e-623f-4091-be8a-987f38d080dc` still returned status `completed` and a report with 11 sources. `POST /api/v1/evidence/search` for that `research_id` returned 5 matches; the first URL was `https://www.mindstudio.ai/blog/what-are-ai-coding-agents`. **Status:** [x]

### AC-06: private port exposure (blocking)

**Criterion:** API, PostgreSQL and Qdrant are not published; Web is bound to loopback.

```bash
python3 scripts/check_release_compose.py
nr ps --format '{{.Service}} {{.Ports}}'
```

**Pass:** the static check exits 0; the only published port is `127.0.0.1:3000->3000/tcp` on `web`. Smoke step 5 checks the same thing on a running stack.
**Evidence:** `python3 scripts/check_release_compose.py` exited 0 (`all release compose checks passed`). `ps --format '{{.Service}} {{.Ports}}'` showed `api 8000/tcp`, `postgres 5432/tcp`, `qdrant 6333-6334/tcp`, and `web 127.0.0.1:3000->3000/tcp`. Smoke step 5: only web published, on `127.0.0.1:41089`. **Status:** [x]

### AC-07: no baked or leaked secrets (blocking)

**Criterion:** no credentials in images, the browser bundle, committed templates or standard logs.

```bash
scripts/release-smoke.sh   # steps 2 and 11
git grep -nE '(^|[^A-Za-z0-9])(sk|tvly)-[A-Za-z0-9]{8,}' -- . ':!*.lock' ':!*package-lock.json' || echo "no key-shaped strings"
grep -E '^(POSTGRES_PASSWORD|NEXUS_OPENAI_API_KEY|NEXUS_TAVILY_API_KEY)=' .env.release.example
git check-ignore .env.release
```

**Pass:** smoke steps 2 and 11 pass; `git grep` finds no real key; the three template values are empty; `.env.release` is ignored.
**Evidence:** Smoke steps 2 and 11 passed (non-root images, no `.env` in the images, no secret-like environment, no server settings in the browser bundle, no password, provider key or database URL in service logs). `git grep` printed `no key-shaped strings`. The three template values in `.env.release.example` are empty. `git check-ignore` reports `.gitignore:6:.env.*` for `.env.release`. **Status:** [x]

### AC-08: offline CI and evaluation baseline (blocking)

**Criterion:** the offline CI suite and the evaluation baseline pass.

```bash
cd apps/api
uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
uv run python -m nexus.evaluation --suite offline --output /tmp/nexus-eval-offline.json
diff -u ../../docs/evaluation/baseline.json /tmp/nexus-eval-offline.json
cd ../web
npm ci && npm run lint && npm run typecheck && npm test && npm run build
```

**Pass:** jobs API, Web and "Release images and smoke" are green on the release commit; `diff` prints nothing. `baseline.json` was not edited to make a check pass.
**Evidence:** [Run 39450946288](https://github.com/maxdudko/nexus-research/actions/runs/39450946288) on `7096289ac8ee4b9ecf49e7002f088a8ba8013d35`: API, Web, and Release images and smoke all `success`. Local `uv run python -m nexus.evaluation --suite offline --output /tmp/nexus-eval-offline.json` then `diff -u` against `docs/evaluation/baseline.json` printed nothing. `baseline.json` was not edited. **Status:** [x]

### AC-09: documentation walkthrough (blocking)

**Criterion:** README, deployment, operations, limitations, demo and changelog match the real commands.

- From a clean clone and with no existing `nexus-release` containers or volumes, follow only [`README.md`](../../README.md) and [`deployment.md`](deployment.md) to a healthy stack, then `down` and `down -v`.
- Follow one procedure from [`operations.md`](operations.md) (for example, the stale-run query).
- Check that every relative link in `README.md`, `CHANGELOG.md` and `docs/release/*.md` resolves.

**Pass:** every command ran as written, or the docs were corrected and the walkthrough repeated. Note any deviation.
**Evidence:** From `/tmp/nexus-rc` at `7096289ac8ee4b9ecf49e7002f088a8ba8013d35`, with no `nexus-release` containers or volumes after `down -v`, the README / deployment `config`, `up -d --build`, `ps` and ready `curl` produced the healthy stack in AC-02. The operations stale-run query returned 0 rows (before the demo). 58 relative links in `README.md`, `CHANGELOG.md` and `docs/release/*.md` resolved, including heading anchors. `down` and `down -v` then exited 0 and `ps -a` was empty.
Deviation: this machine's `.env.release` sets `POSTGRES_USER=admin`, so the documented `psql -U nexus` failed with `role "nexus" does not exist`. `.env.release.example` sets `POSTGRES_USER=nexus`. The same query as `admin` returned the Alembic head. No template change. **Status:** [x]

### AC-10: manual live demo (required for a *demonstrated* release)

**Criterion:** one successful real research demo and one controlled cancel, recorded with actual outcomes.

Follow [`demo.md` Part B](demo.md#part-b-manual-paid-provider-demo) with real keys and approved spend. Paste the filled evidence record here.

**Pass:** Scenario 1 ends with a stored report (`completed`, `partial` or `insufficient_evidence`) whose citations were checked, and Scenario 3 stores `interrupted` (or `running`, recorded as observed). The outcomes are recorded exactly as seen. A mocked smoke run does not count.
**Evidence:**

```text
Date (UTC): 2026-10-20
Commit: 7096289ac8ee4b9ecf49e7002f088a8ba8013d35
Operator: maxdudko
Model (NEXUS_OPENAI_MODEL): absent in .env.release, so the API default gpt-4o-mini
Non-default settings: POSTGRES_USER=admin (example is nexus). NEXUS_TAVILY_USD_PER_SEARCH is set but empty. NEXUS_VERSION=0.1.0.

Scenario 1 — web research
  Question: How do AI coding agents improve software development workflows?
  run_id: 1d15319e-623f-4091-be8a-987f38d080dc
  Final status: completed
  Stages observed: Planning the research; Searching for sources; Indexing sources (4 pages); Selecting evidence; Checking coverage. Heartbeat updated (14s ago at elapsed 33s). The UI then opened the report; a separate synthesis row was not seen before that navigation.
  Number of citations / sources: 11 sources (https). Warnings: page_failed. Usage on the run: iterations 2, search_calls 6, llm_calls 12, pages_indexed 4, pages_failed 1.
  Citation checked (URL) and judgement: E1 opened a new tab to https://www.mindstudio.ai/blog/what-are-ai-coding-agents (HTTP 200, title "What Is an AI Coding Agent? How They Work and When to Use Them"). The page is about AI coding agents. Sentence-level entailment was not checked.
  Refresh and history reload OK (yes/no): yes. Refresh showed Status: Completed and the same report. History on / listed the question as Completed · 10/20/2026, 10:24:37 PM.
  research.usage coverage / estimated_usd: partial / 0.00468848
  Notes: research_id 56f099aa-d8df-4563-99a2-1185b80e1d0f.

Scenario 2 — text PDF (or "not run")
  PDF (name, pages, origin): not run
  run_id:
  Final status:
  Page label checked against the file:
  Notes:

Scenario 3 — cancellation
  Attempt A, UI Stop: run_id a7394b68-d609-41e0-9d07-b65492543244. Question: What limits the reliability of AI coding agents in production? Planning row was visible and the Stop button was on screen. The click did not complete. Stored status: partial (warnings page_failed, duplicate_query_batch).
  Attempt B, client disconnect through Web: run_id 5689ec0d-70bc-4962-b4ac-3b3d00c92ea3. Question: Which practices reduce defects from AI-generated code reviews? The stream returned event research.started and event research.progress, then the client closed the connection. Stored status: interrupted.
  Notes: An earlier stream, run d05b3bc5-b8c5-4677-bf98-9b698935603a (How do teams review code produced by AI coding agents?), was meant to be closed at the first progress event but the client kept reading and the stored status was partial.

Persistence after restart
  checkpoints before / after: 35 / 35
  Run detail after restart OK (yes/no): yes (completed, 11 sources)
  Evidence search matches after restart: 5
```

**Status:** [x]

### AC-11: reviewed tag and release notes (blocking)

**Criterion:** `v0.1.0` points to the reviewed release commit, and the GitHub release notes state the limitations.

**Pass:** the maintainer approved the SHA, the annotated tag points to it, and the release notes include the known limitations below.
**Evidence:** Pending. Tag `v0.1.0` and the GitHub release are created only after CI is green on this documentation commit. **Status:** [ ]

## Decision

| Outcome | Condition |
|---|---|
| **GO** | AC-01 to AC-11 all pass with recorded evidence, and there is no known critical security, boot or data-loss defect. |
| **NO-GO** | Any of: a publicly exposed anonymous API, missing or failing init, broken SSE, unverified clean startup, leaked secrets, failing offline gate, or a tag on an unreviewed commit. |
| **Conditional** | Everything except AC-10 passes because paid provider access is unavailable. Publish at most a packaging candidate (for example `v0.1.0-rc.1`), and do not call it a demonstrated `v0.1.0`. |

**Decision:** GO for AC-01 through AC-10. AC-11 is the tag and GitHub release on this commit after its CI run is green. No known critical security, boot or data-loss defect. The unhealthy API on the first `up` was a reused local container, and a fresh `down -v` / `up` was healthy. **Decided by:** maxdudko **Date:** 2026-10-20

## After a GO (maintainer only)

> **Do not run without explicit maintainer approval.** These commands publish the release.

1. Date the `[0.1.0]` entry in [`CHANGELOG.md`](../../CHANGELOG.md), set `Status: Accepted` in [`ADR-007`](../adr/ADR-007.md) and the Phase 7 spec, and mark Phase 7 done in [`roadmap.md`](../roadmap.md). Commit, and confirm CI is green on that commit. That commit is the release commit.
2. Tag and push:

   ```bash
   git tag -a v0.1.0 <release-sha> -m "Nexus Research v0.1.0"
   git push origin v0.1.0
   ```

   Run `python3 scripts/check_release_version.py --tag v0.1.0` before tagging. After the push, wait for the Release workflow to go green. Record its run link and the `SHA256SUMS` from the `release-images-<version>` artifact as release evidence.

3. Create the GitHub release from the tag with the notes below, edited to match the recorded evidence.
4. Open the Alpha milestone for Phases 8–10 ([roadmap](../roadmap.md#after-the-mvp-alpha-v020)). Do not implement any of it on the release branch.

## Draft release notes

```markdown
# Nexus Research v0.1.0

First MVP release: a single-operator deep-research agent. It plans a question,
searches the web, indexes pages and an optional text PDF, and writes a cited report,
with live progress in a Next.js UI.

## Run it

    cp .env.release.example .env.release   # set POSTGRES_PASSWORD and provider keys
    docker compose --env-file .env.release -f compose.release.yml up -d --build

Open http://127.0.0.1:3000. See docs/release/deployment.md.

## Verification

- CI: <link to the green run on the release commit> (API, Web, release images and
  packaged smoke with a mocked model; no paid calls).
- Offline evaluation matches docs/evaluation/baseline.json (frozen fixtures, not
  live retrieval quality).
- Manual live demo: <date, model and observed outcomes from the checklist, or
  "not performed">.

## Known limitations

- No authentication or per-user isolation. Do not expose it publicly; use an SSH
  tunnel or VPN for remote access.
- Research runs only while the request is open. There is no background execution
  or resume, and an interrupted run can stay "running".
- PDF originals are not stored, there is no OCR, and PDF citations are page labels.
- Cost figures are partial estimates in logs only.
- Single host, linux/amd64 only, no high availability.

Full list: docs/release/limitations.md. Changes: CHANGELOG.md.
```
