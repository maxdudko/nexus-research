# Phase 7 — Release Specification

**Project:** Nexus Research  
**Target release:** MVP `v0.1.0`  
**Status:** Accepted  
**Date:** 2026-10-19  
**Related:** `docs/roadmap.md`, ADR-003–006, `docs/evaluation/methodology.md`, `docs/evaluation/failure-matrix.md`  
**Implementation branch:** `development`

## 1. Objective

Package the existing single-operator deep-research MVP into a reproducible, verifiable, documented release that another developer can clone, configure, run, evaluate and stop without manually starting Python and Node processes. Preserve the research workflow, API/SSE contracts, evidence semantics and evaluation baseline.

Phase 7 is **release engineering, not product expansion**. The exit milestone is a tagged, demonstrated `v0.1.0`; multi-user Alpha follows separately.

## 2. Verified baseline (development branch, 2026-10-19)

- Root `docker-compose.yml` currently defines PostgreSQL 18.6, Qdrant v1.19.2 and optional Jaeger 2.22.0. Database ports bind to host loopback; API and Web are **not** Compose services yet.
- API is Python `>=3.14,<3.15`, `uv`, FastAPI, LangGraph, SQLAlchemy/Alembic, OpenAI/Tavily. `apps/api/pyproject.toml` is version `0.1.0`.
- Web is Next.js 16.4.2 / React 19.3.0, Node 24; `apps/web/package.json` is version `0.1.0`.
- Web's `API_PROXY_TARGET` currently defaults to `http://127.0.0.1:8000`; proxy forwards SSE bodies without buffering. In Compose, it must resolve the API service hostname instead.
- API config validates `NEXUS_*`; `production` disables OpenAPI docs; readiness checks PostgreSQL and Qdrant; liveness is independent.
- Checkpoint tables are initialized with `uv run python -m nexus.research.checkpoint` and are **not** managed by Alembic. Alembic owns domain schema. Both initialization paths must be documented and verified.
- `.github/workflows/ci.yml` runs API lint/typecheck/tests, offline evaluation + baseline diff, Alembic upgrade, and Web lint/typecheck/tests/build. No container build or end-to-end packaged smoke gate yet.
- The README explicitly warns: no authentication; `research_id` is an evidence scope, **not** tenant authorization; do not expose the app publicly.
- Research is request-scoped. SSE cancellation, timeout, process loss and checkpoint state do not imply background execution or automatic resume.
- PDF bytes are not retained; scanned PDFs have no OCR; PDF citations are page labels rather than durable links to original bytes.

These are source-grounded observations. Container paths, health checks and migration commands below are **Phase 7 design requirements**, not assertions that they already exist.

## 3. Scope

### In scope

1. Reproducible API and Web container images with pinned lockfile installation, non-root runtime and predictable startup.
2. One root Compose release configuration, while preserving convenient development infrastructure startup.
3. Explicit one-shot database migration/checkpoint initialization with idempotency and clear failure behavior.
4. Environment templates and operator documentation for local and private/demo deployment.
5. Network and secret hygiene; non-public default exposure; startup/readiness/liveness checks; graceful shutdown.
6. Container smoke tests, CI build verification, deterministic offline evaluation and release evidence.
7. Updated README, architecture/limitations/operations docs, demo script, changelog and annotated `v0.1.0` release process.

### Out of scope

- User accounts, authorization, `user_id` tenancy, organizations, admin UI, landing page, redesign or billing.
- Public anonymous SaaS; CDN/WAF as a substitute for authorization.
- Background workers, Redis, queue, scheduled research, automatic resume, multi-host HA or Kubernetes.
- Changing the graph topology, model/prompt behavior, SSE event schema, report contract, evidence collection format, Qdrant vector size or evaluation labels.
- Requiring Jaeger/OTLP, Prometheus, hosted evaluation dashboards or paid live tests in CI.

## 4. Users and operating modes

**Primary:** developer evaluating the MVP on a workstation.  
**Secondary:** maintainer operating a private demonstration on a single machine.

**Mode A — local Compose:** start all runtime services with a documented command; Web is accessible only on host loopback by default; API/Postgres/Qdrant not published to the public network. Provider keys remain server-side.

**Mode B — private remote demo (optional):** same images and architecture behind operator-managed VPN/SSH tunnel or an independently configured authentication gate. An HTTPS reverse proxy alone does **not** solve anonymous access to billable API endpoints. No unauthenticated public deployment is endorsed.

## 5. Functional requirements

### FR-01 — Image builds

- Provide `apps/api/Dockerfile` and `apps/web/Dockerfile` (or documented equivalent) with deterministic build inputs, explicit Python 3.14 and Node 24 base image versions, and committed lockfiles (`uv.lock`, `package-lock.json`). Prefer digest-pinned base images if practical; document refresh procedure.
- Use multi-stage builds where useful; runtime images contain only necessary dependencies/artifacts. API must import installed `nexus` package; Web must start its production Next.js server. Do not rely on bind-mounted source in release mode.
- Runtime user is non-root. Filesystem writable locations are deliberate; no provider credentials or `.env` files baked into images.
- Use `.dockerignore` to exclude `.env`, `.env.local`, `.venv`, `node_modules`, `.git`, caches, evaluation reports containing sensitive data and local artifacts.
- Support amd64 as required MVP target; document any other tested architecture. Avoid implying multi-arch support without CI proof.

### FR-02 — Compose runtime

- Provide a documented command to launch **Web, API, PostgreSQL, Qdrant** in a private default configuration. Optional Jaeger remains opt-in.
- Preserve existing developer workflow (`docker compose up -d` for infra) or explicitly migrate it with documented compatibility. Recommended: retain `docker-compose.yml` as development infrastructure, add `compose.release.yml` as a full, explicitly invoked stack. If choosing another layout, ADR and README must match actual commands.
- The Web proxy resolves API by Compose DNS (`http://api:8000`); API binds `0.0.0.0` **inside** its container. API, PostgreSQL and Qdrant use internal service DNS; no `localhost` cross-container assumptions.
- Default Web port mapping binds `127.0.0.1:3000:3000`; API, DB and Qdrant have no published ports in the release configuration. Use a private bridge network and named volumes.
- PostgreSQL and Qdrant have persistent volumes; health checks or explicit readiness waits prevent starting a usable UI against unavailable dependencies. `depends_on` alone does not guarantee successful migrations or a healthy application.
- Compose must not ship the development password `nexus` as a production default. Missing release secrets fail validation/startup with clear, non-sensitive errors. No checked-in release secrets.

### FR-03 — Schema and checkpoint initialization

- Provide a single, documented, one-shot initialization path for Alembic domain migrations **and** LangGraph PostgreSQL checkpoint setup, respecting their distinct ownership.
- Explicitly determine whether `nexus.research.checkpoint` is safe to rerun; test twice against fresh persistent DB and after restart. Never assume checkpoint setup is equivalent to `alembic upgrade head`.
- Recommended Compose design: one-shot `init` service, built from API image, waiting for healthy PostgreSQL; executes `alembic upgrade head` then checkpoint setup and exits successfully. API/Web start only after successful init and service health checks.
- Initialization failure blocks release startup; no silent fallback to an uninitialized schema. Do not auto-run destructive downgrades or reset volumes.
- Document existing-volume upgrades, schema backup guidance and recovery steps. Do not promise zero-downtime upgrades.

### FR-04 — Configuration and secrets

- Provide a release env template with documented required, optional and development-only variables. Include PostgreSQL credential setup, `NEXUS_DATABASE_URL`, `NEXUS_QDRANT_URL`, OpenAI/Tavily keys, `API_PROXY_TARGET`, graph budgets and OTel toggles.
- Explain the distinction between Compose interpolation variables and API `NEXUS_*` runtime settings. Do not accidentally expose provider keys to the browser via `NEXT_PUBLIC_*`, Web client bundles or browser-visible config.
- Use `NEXUS_ENVIRONMENT=production` for release mode; verify docs/OpenAPI routes are disabled as designed. Configure sensible log and OTel defaults (tracing off).
- Ensure API/Next.js proxy timeouts accommodate `NEXUS_GRAPH_DEADLINE_SECONDS` + grace. Verify SSE streaming/heartbeats through the actual packaged stack and any documented reverse proxy.
- Secret rotation instructions must explain process restart and no credentials in logs. Do not claim Docker environment variables are equivalent to a dedicated secret manager.

### FR-05 — Health, startup, shutdown and failure modes

- API `/api/v1/health/live` must work without external dependencies; `/api/v1/health/ready` must report unavailable PostgreSQL/Qdrant with safe error data.
- Container health checks use local URLs and bounded timeouts; Web health check must exercise a real production route or documented lightweight endpoint, not merely process existence.
- Verify shutdown signals reach Uvicorn and Next.js; stop/restart preserves named volumes. A request interrupted by shutdown may be marked interrupted or remain stale `running` per existing contract; **no automatic resume**.
- Document logs (`docker compose logs`), health inspection, common provider 429/timeouts, partial/insufficient reports, cancellation and stale run behavior.

### FR-06 — Release verification

- CI continues existing Python 3.14 lint/typecheck/pytest, Web lint/typecheck/test/build, Alembic, offline evaluation baseline diff.
- Add container build checks for both images on PRs (without provider keys). A fresh-stack smoke path must run at least in a documented repeatable script; ideally gate release workflow or CI if reliable and reasonably fast.
- Smoke script must verify: Compose config valid; migrations + checkpoint setup succeed; API live/ready; Web accessible; proxy reaches API; browser-facing route renders; no external provider call required for infrastructure smoke.
- Add a manual opt-in provider demo checklist: research question, stage/progress SSE, terminal report with citations, refresh history, PDF upload, interrupted run. Record model/date/cost where available. Do not require paid calls in PR CI.
- Re-run offline evaluation and confirm `docs/evaluation/baseline.json` matches generated artifact. Do not imply its Recall@K measures real OpenAI/Qdrant retrieval quality.
- Include an image/config secret-leak check and a check that release Compose does not publish API/DB/Qdrant ports.

### FR-07 — Documentation and release artifacts

- Rewrite README status from “planned” to implemented MVP; provide quickstart, architecture, prerequisites, provider-key expectations, local-only security warning and links to docs.
- Add `docs/release/deployment.md`, `docs/release/operations.md`, `docs/release/demo.md`, `docs/release/limitations.md`, and a concise `CHANGELOG.md` (file names may be adjusted with matching links).
- Explain research lifecycle: request-scoped SSE, no resume, checkpoint versus domain history, PDF non-persistence, page-only PDF citations, evidence `research_id` isolation (not authentication), quotas, partial results, cost estimate coverage and evaluation methodology.
- Provide a representative demo with one web question and one text-PDF scenario; keep fixtures and expected outcomes descriptive, not fabricated. Screenshots optional; no requirement for landing-page design.
- Document release commands: tests, build, config validation, clean install, upgrade, rollback limitations, tag and GitHub release notes. Never automatically push/tag as part of CI without explicit maintainer action.

## 6. Nonfunctional requirements

- **Reproducibility:** lockfile installs; identical source revision and configuration build the same application dependency graph (byte-for-byte image reproducibility not claimed).
- **Security:** loopback-only entry point by default, internal service network, no baked secrets, non-root images, no anonymous public service.
- **Reliability:** health/readiness, bounded resource use, explicit init, persistent storage and predictable restart behavior.
- **Privacy:** observability allowlists and no raw prompts/PDF text in routine logs.
- **Cost:** no automatic paid evaluation, no unexpected background OpenAI/Tavily jobs, no fabricated complete cost totals.
- **Operability:** operator can diagnose failure without modifying application source.

## 7. Acceptance criteria (release gates)

| ID | Gate | Evidence |
|---|---|---|
| AC-01 | Fresh clone can build both images without local Python/Node | documented build command + CI logs |
| AC-02 | Full stack starts with one documented Compose invocation after configuration | `docker compose ... config`, `up`, `ps` |
| AC-03 | Domain migrations and checkpoint setup run on fresh DB and are repeatable | init logs + integration/smoke test |
| AC-04 | API ready and Web/proxy reachable; SSE is not buffered | HTTP smoke + manual research demo |
| AC-05 | Restart preserves history, checkpoint and Qdrant evidence | persistence smoke |
| AC-06 | Release API/DB/Qdrant not host-published; Web bound to loopback | Compose inspection/test |
| AC-07 | No credentials in images, browser bundle, committed templates or standard logs | inspection + tests |
| AC-08 | Offline CI suite and evaluation baseline pass | GitHub Actions artifact/logs |
| AC-09 | README, limitations, operations, demo and changelog match real commands | maintainer walkthrough |
| AC-10 | One successful real research demo and one controlled failure/cancel demo recorded | manual checklist with actual outcomes |
| AC-11 | `v0.1.0` tag points to reviewed release commit; GitHub release notes state limitations | release checklist |

**Blocking:** AC-01 through AC-09 and AC-11. AC-10 is required for declaring a *demonstrated* MVP release; if provider access is unavailable, do not claim this gate passed. No invented metrics.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Anonymous billable endpoints exposed | loopback-only default, private demo only; defer public SaaS to Alpha auth |
| Cross-container `localhost` misconfiguration | Compose DNS, explicit integration smoke |
| Schema migration/checkpoint initialization race | one-shot init dependency and repeatability tests |
| Next.js SSE buffered or timed out | real proxy streaming test and timeout documentation |
| Development credentials reused in release | mandatory non-default secret input and config validation |
| Restart loses an in-flight request | document no-resume semantics; verify persisted terminal/stale state behavior |
| Missing original PDF bytes | limitations and demo expectations |
| Hidden cost/quality assumptions | cite evaluation methodology and partial cost coverage |

## 9. Deferred Alpha backlog

- Phase 8: identity, authentication, user-owned research data, authorization across PostgreSQL/Qdrant/PDF/history/SSE.
- Phase 9: research UX redesign, user dashboard, onboarding.
- Phase 10: restricted admin/evaluation UI, comparisons, usage analytics.
- Alpha v0.2.0: controlled user onboarding, landing and product validation.

No Alpha feature is required to pass Phase 7.

## 10. Open decisions for implementation planning

1. Verify exact current checkpoint CLI idempotency and whether init requires additional permissions.
2. Confirm Next.js production output mode (`standalone` or standard `next start`) and chosen image layout.
3. Decide whether release Compose lives in `compose.release.yml` or an alternative explicitly named file; preserve developer workflow.
4. Choose how operators supply database secrets locally (env-file vs Compose secrets) without overclaiming protection.
5. Decide if packaged smoke runs in every PR or in a dedicated release gate; in either case record proof before tagging.

Resolve these in Step 1 design review; do not silently assume implementation details.
