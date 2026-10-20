# Release checklist: `v0.1.0` (Gate C)

This checklist decides whether `v0.1.0` can be tagged. Acceptance criteria come from [`docs/specs/phase-7-release/spec.md`](../specs/phase-7-release/spec.md) section 7; the decision rules come from the Gate C section of [`plan.md`](../specs/phase-7-release/plan.md).

Rules for filling it in:

- Record only evidence that was actually observed: a CI link, a command and its output, or a filled demo record. Never write expected results into an evidence field.
- Every item must pass on the **release commit**, the exact SHA that will be tagged. Evidence from an earlier commit is a baseline, not a pass.
- Tagging and publishing are manual maintainer actions. Nothing in CI tags, pushes or publishes a release.

## Release record

| Field | Value |
|---|---|
| Release commit (SHA) | _to fill_ |
| Branch | `development` |
| Date (UTC) | _to fill_ |
| CI run on the release commit | _to fill_ |
| Offline baseline | `docs/evaluation/baseline.json`, dataset `2026-10-19.2`, report version `1` |
| Images | `nexus-api:0.1.0`, `nexus-web:0.1.0`, built locally from the release commit (not published to a registry) |
| Tested platform | linux/amd64 |
| Reviewer | _to fill_ |

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
**Evidence:** _to fill_ **Status:** [ ]

### AC-02: full-stack startup (blocking)

**Criterion:** one documented Compose invocation starts the stack after configuration.

```bash
cp .env.release.example .env.release && chmod 600 .env.release   # set POSTGRES_PASSWORD
nr config --quiet
nr up -d --build
nr ps -a
```

**Pass:** `config` exits 0; `postgres`, `qdrant`, `api` and `web` are `healthy`; `init` is `Exited (0)`.
**Evidence:** _to fill_ **Status:** [ ]

### AC-03: migrations and checkpoint setup, repeated (blocking)

**Criterion:** domain migrations and checkpoint setup run on a fresh database and can be repeated.

```bash
nr logs init | grep -E 'init - (domain schema at head|checkpoint tables ready)'
nr run --rm init
nr exec -T postgres psql -U nexus -d nexus -Atc "select version_num from alembic_version"
```

**Pass:** both `init -` lines appear on the fresh stack; the rerun exits 0; the Alembic head is unchanged. `release-smoke.sh` step 6 checks the same thing automatically.
**Evidence:** _to fill_ **Status:** [ ]

### AC-04: API, Web, proxy and SSE (blocking)

**Criterion:** the API is ready, Web and its proxy are reachable, and SSE is not buffered.

```bash
curl -fsS http://127.0.0.1:3000/api/v1/health/live
curl -fsS http://127.0.0.1:3000/api/v1/health/ready
scripts/release-smoke.sh
```

**Pass:** live returns `{"status":"ok"}`; ready returns `{"status":"ok","postgres":"ok","qdrant":"ok"}`; the smoke prints `all release smoke checks passed`, which includes heartbeats arriving through Web before the stream ends (step 7). Progress events from a real run are confirmed under AC-10.
**Evidence:** _to fill_ **Status:** [ ]

### AC-05: persistence across restart (blocking)

**Criterion:** a restart keeps history, checkpoints and Qdrant evidence.

- History: `release-smoke.sh` step 10 (automatic).
- Checkpoints and evidence: **not covered by the smoke test.** Run the "Persistence after restart" steps in [`demo.md`](demo.md#persistence-after-restart-ac-05) after a real run. The evidence search needs an OpenAI key.

**Pass:** smoke step 10 passes; the `checkpoints` count is unchanged across `down` / `up`; the run detail still returns its report; the evidence search for that run's `research_id` returns at least one match.
**Evidence:** _to fill_ **Status:** [ ]

### AC-06: private port exposure (blocking)

**Criterion:** API, PostgreSQL and Qdrant are not published; Web is bound to loopback.

```bash
python3 scripts/check_release_compose.py
nr ps --format '{{.Service}} {{.Ports}}'
```

**Pass:** the static check exits 0; the only published port is `127.0.0.1:3000->3000/tcp` on `web`. Smoke step 5 checks the same thing on a running stack.
**Evidence:** _to fill_ **Status:** [ ]

### AC-07: no baked or leaked secrets (blocking)

**Criterion:** no credentials in images, the browser bundle, committed templates or standard logs.

```bash
scripts/release-smoke.sh   # steps 2 and 11
git grep -nE '(^|[^A-Za-z0-9])(sk|tvly)-[A-Za-z0-9]{8,}' -- . ':!*.lock' ':!*package-lock.json' || echo "no key-shaped strings"
grep -E '^(POSTGRES_PASSWORD|NEXUS_OPENAI_API_KEY|NEXUS_TAVILY_API_KEY)=' .env.release.example
git check-ignore .env.release
```

**Pass:** smoke steps 2 and 11 pass; `git grep` finds no real key; the three template values are empty; `.env.release` is ignored.
**Evidence:** _to fill_ **Status:** [ ]

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
**Evidence:** _to fill_ **Status:** [ ]

### AC-09: documentation walkthrough (blocking)

**Criterion:** README, deployment, operations, limitations, demo and changelog match the real commands.

- From a clean clone and with no existing `nexus-release` containers or volumes, follow only [`README.md`](../../README.md) and [`deployment.md`](deployment.md) to a healthy stack, then `down` and `down -v`.
- Follow one procedure from [`operations.md`](operations.md) (for example, the stale-run query).
- Check that every relative link in `README.md`, `CHANGELOG.md` and `docs/release/*.md` resolves.

**Pass:** every command ran as written, or the docs were corrected and the walkthrough repeated. Note any deviation.
**Evidence:** _to fill_ **Status:** [ ]

### AC-10: manual live demo (required for a *demonstrated* release)

**Criterion:** one successful real research demo and one controlled cancel, recorded with actual outcomes.

Follow [`demo.md` Part B](demo.md#part-b-manual-paid-provider-demo) with real keys and approved spend. Paste the filled evidence record here.

**Pass:** Scenario 1 ends with a stored report (`completed`, `partial` or `insufficient_evidence`) whose citations were checked, and Scenario 3 stores `interrupted` (or `running`, recorded as observed). The outcomes are recorded exactly as seen. A mocked smoke run does not count.
**Evidence:** _to fill_ **Status:** [ ]

### AC-11: reviewed tag and release notes (blocking)

**Criterion:** `v0.1.0` points to the reviewed release commit, and the GitHub release notes state the limitations.

**Pass:** the maintainer approved the SHA, the annotated tag points to it, and the release notes include the known limitations below.
**Evidence:** _to fill_ **Status:** [ ]

## Decision

| Outcome | Condition |
|---|---|
| **GO** | AC-01 to AC-11 all pass with recorded evidence, and there is no known critical security, boot or data-loss defect. |
| **NO-GO** | Any of: a publicly exposed anonymous API, missing or failing init, broken SSE, unverified clean startup, leaked secrets, failing offline gate, or a tag on an unreviewed commit. |
| **Conditional** | Everything except AC-10 passes because paid provider access is unavailable. Publish at most a packaging candidate (for example `v0.1.0-rc.1`), and do not call it a demonstrated `v0.1.0`. |

**Decision:** _to fill_ **Decided by:** _to fill_ **Date:** _to fill_

## After a GO (maintainer only)

> **Do not run without explicit maintainer approval.** These commands publish the release.

1. Date the `[0.1.0]` entry in [`CHANGELOG.md`](../../CHANGELOG.md), set `Status: Accepted` in [`ADR-007`](../adr/ADR-007.md) and the Phase 7 spec, and mark Phase 7 done in [`roadmap.md`](../roadmap.md). Commit, and confirm CI is green on that commit. That commit is the release commit.
2. Tag and push:

   ```bash
   git tag -a v0.1.0 <release-sha> -m "Nexus Research v0.1.0"
   git push origin v0.1.0
   ```

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
