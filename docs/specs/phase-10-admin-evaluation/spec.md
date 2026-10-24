# Phase 10 — Admin & Evaluation Specification

**Project:** Nexus Research  
**Target:** Alpha `v0.2.0` / Phase 10  
**Status:** Gate 0 accepted 2026-10-24 — Step 1 may start  
**Baseline:** `development` at `9fb4f096606f59f61f47713c8f8f7f0b71e7c4c5`  
**Dependencies:** Phase 8 identity and ownership; Phase 9 authenticated application shell  
**Normative architecture:** [ADR-010](../../adr/ADR-010.md)  
**Execution:** [plan.md](plan.md)  
**Review:** [gate-0.md](gate-0.md)  
**Related:** [Alpha vision](../alpha-v0.2.0-vision.md), [roadmap](../../roadmap.md), [ADR-008](../../adr/ADR-008.md), [ADR-009](../../adr/ADR-009.md), [evaluation methodology](../../evaluation/methodology.md)

## 1. Problem and objective

The MVP already has a deterministic offline evaluation suite, an opt-in LLM grounding judge, versioned Pydantic result schemas, calibration/confusion-matrix calculation, structured logs, partial usage/cost estimation, and persisted research history. What it does **not** have is a restricted, persistent operator view of evaluation history, reproducible comparisons, or safe aggregate run diagnostics.

**Goal:** let an authenticated platform administrator inspect versioned evaluation results, compare compatible measurements, and understand basic research-run health and usage, without accessing private user research content or mistaking fixture metrics for production quality.

**Success statement:** an operator can import CLI-produced evaluation artifacts, browse their immutable history in `/admin`, compare compatible runs with explicit provenance, inspect non-sensitive diagnostic aggregates, and see coverage/unknowns on usage. A normal account cannot retrieve any admin data through API or UI.

## 2. Approved Gate 0 product decisions

1. **Evaluation execution:** existing CLI remains the only execution path; a separate, local/operator CLI imports artifacts. No HTTP/UI evaluation execution in Alpha.
2. **Persistence:** PostgreSQL tables `evaluation_runs` and `evaluation_case_results`, with typed/allowlisted JSONB for limited result payloads.
3. **Privacy:** administrator sees aggregates, safe technical metadata, and authored/sanitized evaluation results; no implicit read access to user questions, reports, PDFs, source excerpts, raw prompts, or checkpoint state.
4. **Cost analytics:** basic known usage metrics, estimates only where supported by durable inputs, and explicit `complete` / `partial` / `unavailable` coverage. No billing or inferred zero-cost values.

Technical choices and trade-offs are recorded in ADR-010. Gate 0 accepted that contract on 2026-10-24. Step 1 may start. This acceptance is not release approval.

## 3. Verified starting point (development branch, inspected 2026-10-24)

- `apps/api/src/nexus/evaluation/{__main__,runner,schemas,datasets,retrieval,citations,grounding,judge,calibration}.py`: offline and opt-in live suites; result schemas `OfflineReport` and `LiveReport`, `report_version=1`, dataset `2026-10-19.2`, `K=5`.
- `docs/evaluation/baseline.json`: deterministic fixture regression artifact; no run timestamp, commit SHA, or dataset content fingerprint. The CI byte comparison is an existing contract.
- `docs/evaluation/outputs/nexus-eval-live.json`: existing informational judge artifact with calibration; the existing static HTML dashboard is a design reference, **not** an authenticated application.
- `apps/api/src/nexus/api/deps.py`: `require_admin` exists and does not bypass research ownership.
- `apps/api/src/nexus/models/run.py`: `ResearchRun` stores status, error code, timestamps, and optional graph `usage`; successful reports are stored separately. Research history is user-scoped through `ResearchScope`.
- `apps/api/src/nexus/observability/{usage,costs,pricing,logging}.py`: request-local operation usage and redacted logs; per-operation cost data are **not** a durable queryable cost ledger.
- `apps/web/src/app/(app)` and ADR-009: existing authenticated shell to extend rather than replace.

**Baseline interpretation:** the frozen `Recall@5=0.833333`, `Precision@5=0.240000`, and `MRR@5=0.705556` measure authored fixture rankings, not production retrieval. The existing live artifact has `11/13` judge-label agreements; this is small-dataset calibration, not overall factual accuracy.

## 4. Scope

### In scope — required

- Read-only, backend-authorized `/api/v1/admin/*` routes and an admin-only `/admin` UI using the existing authenticated shell.
- Persistent, immutable evaluation run and case history, validated before import, with dataset fingerprint, report/scorer versions, configuration, artifact hash, and provenance level.
- A local/operator-only import CLI for existing `offline` and `live` JSON reports; import does not invoke providers or modify the original artifact.
- Idempotent reimport, atomic persistence, validation of cases and aggregate consistency, rejection of unsupported schema versions, and explicit provenance limitations for legacy artifacts.
- Offline baseline comparison and live judge comparison with strict compatibility checks and reasons when comparison is blocked.
- Case-level retrieval/citation/grounding summaries; confusion matrix and agreement **only** for live labeled judge cases where available.
- Aggregate research diagnostics: status counts, error-code counts, durations derived from timestamps, stale `running` counts, and bounded lists of safe technical metadata.
- Usage/estimated-cost presentation using existing durable fields, with null/unknown and coverage semantics; no reconstructed historical token ledger.
- Security, migration, metric, import, API, UI, and integration tests; release documentation and Gate A/B/C evidence.

### Out of scope — explicit

- Launching offline/live evaluation from HTTP or UI; background jobs, schedulers, task queues, automatic periodic runs.
- Full production RAG accuracy benchmark, automated truth verification, auto-prompt tuning, or claims that a citation implies factual correctness.
- Arbitrary admin access to another user's research, report, PDF, sources, Qdrant points, or LangGraph checkpoints.
- User management console, billing, invoicing, per-user financial chargeback, provider reconciliation, complete telemetry retention, Prometheus/Grafana replacement.
- Public multi-tenant deployment, enterprise RBAC, organization workspaces, real-time infrastructure health dashboards.

## 5. Functional requirements

### FR-01 — Admin authorization and privacy

- Every admin API endpoint uses `Depends(require_admin)`; missing/expired sessions return `401`, authenticated non-admins return `403`. The server, not the sidebar, enforces access.
- The web `/admin` entry is shown only for an admin and handles anonymous, forbidden, expired, unavailable, and loading states. Browser navigation protection is UX only.
- No endpoint may return `ResearchRun.question`, `ResearchReportRecord.report`, uploaded file content, source text/URLs, raw prompt, excerpt, judge rationale, cookie, token, or provider secret.
- Admin diagnostics may expose opaque `run_id`, sanitized `error_code`, status, and timestamps, but no `owner_user_id`, `research_id`, or account email in the initial contract. Avoid broad ORM serialization; use dedicated response DTOs and explicit SQL columns.
- Imported evaluation data are from authored operator fixtures only; the importer **must** discard free-text claim, excerpt, question, URL, and judge rationale fields even if the source artifact includes them.
- JSON responses are `Cache-Control: no-store`; no sensitive content is placed in query parameters, logs, client persistent storage, or tracing attributes. Preserve ADR-008 and ADR-009 proxy/cookie/CSRF constraints.

### FR-02 — CLI execution and import

- Preserve `uv run python -m nexus.evaluation --suite offline|live --output PATH` and the existing opt-in/live cost safeguards unchanged.
- Add `uv run python -m nexus.admin.evaluation_import --artifact PATH [--fixtures DIR] [--code-revision SHA] [--committed-baseline]`. It uses the API database settings. The accepted flag and hash rules are ADR-010 C1–C2.
- The importer reads local files, rejects an artifact larger than 2,097,152 bytes, validates the existing Pydantic report schema with `extra` policy, validates dataset/case identity, and checks aggregate/calibration consistency. It never calls a model, reads user research, or accepts an HTTP upload.
- Compute `artifact_sha256` over original bytes and `dataset_sha256` over a deterministic manifest of the **five** validated fixture files (filename + content digest in a stable order). A dataset version string alone is insufficient identity.
- Offline imports must re-run the deterministic fixture evaluation against the provided fixture set and compare the normalized report; otherwise reject the import as unverified. Preserve the existing CI baseline byte gate independently.
- Live imports validate case IDs, outcomes, counts, and recomputed calibration against the supplied fixtures, but cannot independently attest provider execution. Label provenance `operator_imported` rather than `provider_verified`.
- Existing artifacts lack a trustworthy execution timestamp or commit provenance. Store `executed_at = null`. Phase 10 has no flag that sets it. Store `imported_at` separately. Store `code_revision = null` unless `--code-revision` supplies a 40-hex SHA, recorded as `operator_asserted` in `code_revision_source`. Never invent timestamps or read `HEAD`.
- Import all run and case records in one DB transaction. On validation failure, write **nothing**. Reimporting the same artifact hash is a no-op returning the existing ID; the same hash with conflicting metadata is an error. An attempted mutation of a stored result is rejected.
- Initial migration of historical artifacts is optional/manual. Do not silently import files on server startup or CI.

### FR-03 — Evaluation persistence

**`evaluation_runs`** (new SQLAlchemy model):

| Field | Type / rule |
|---|---|
| `id` | UUID PK, generated server-side by importer |
| `suite` | `offline` or `live` |
| `outcome` | `passed`, `failed`, or `informational`; offline pass/fail is fixture contract, live is informational |
| `artifact_sha256` | 64-character hex, unique |
| `dataset_version`, `dataset_sha256` | Non-null version and content fingerprint |
| `report_version`, `scorer_version` | Non-null, explicit metric-contract identifiers |
| `k` | Positive integer |
| `code_revision` | Nullable 40-hex Git SHA |
| `code_revision_source` | Null, or `operator_asserted` when `code_revision` is set |
| `model`, `prompt_version`, `temperature`, `pricing_version` | Nullable for offline; validated as appropriate for live |
| `executed_at`, `imported_at` | Nullable execution time, non-null import time |
| `provenance_level` | `offline_recomputed` or `operator_imported` |
| `is_committed_baseline` | True only for explicitly imported, byte-verified committed baseline |
| `summary`, `config` | Small allowlisted JSONB; no raw artifact payload |

**`evaluation_case_results`**:

| Field | Type / rule |
|---|---|
| `evaluation_run_id` | FK to `evaluation_runs.id`, `ON DELETE CASCADE` |
| `category` | `retrieval`, `citation`, `grounding` |
| `case_id` | Authored semantic case ID |
| `result` | Allowlisted, schema-validated JSONB; no free text |

Composite PK/unique `(evaluation_run_id, category, case_id)`; bounded lengths, checks, and indexes for descending history and case lookup. Index `evaluation_runs(imported_at DESC, id DESC)`; unique `artifact_sha256`. No user/research foreign keys: evaluations use synthetic authored fixtures, not private user runs.

Persisted case fields: retrieval metrics and gold count; citation validity, expected validity and bounded error codes; grounding structural validity, expected validity, gold/predicted labels, outcome, model/prompt metadata where relevant, and bounded error code. Store neither `rationale` nor source/claim text. Summaries contain counts, aggregate metrics, and calibration matrix only.

**Immutability:** importer does not update completed rows. DB constraints protect shape/uniqueness; application-level insert-only access is enforced and tested. If stronger tamper resistance is later required, add a DB permission or audit policy under a separate ADR.

### FR-04 — Baseline and comparability

- The committed `docs/evaluation/baseline.json` remains the CI oracle. The admin UI does not change, regenerate, or silently replace it.
- A baseline import must verify artifact bytes against the committed baseline and validate/recompute against the associated fixture set. A baseline is a fixture regression reference, **not** a production performance target.
- A pair is *strictly comparable* only when `suite`, `dataset_sha256`, `dataset_version`, `report_version`, `scorer_version`, `k`, and metric semantics match. Live pairs additionally require identical model, prompt version, temperature, and judge configuration/label taxonomy. Pricing version is relevant to **cost** comparison, not judge-label agreement.
- Different `code_revision` values are allowed and desirable for regression comparisons, provided all metric-contract inputs above match. If provenance is incomplete, display a warning and never attribute the result to an unverified historical commit.
- Incompatible pairs return `comparable: false` and stable reason codes (`dataset_mismatch`, `suite_mismatch`, `scorer_mismatch`, `judge_config_mismatch`, etc.). Do not calculate or display misleading deltas; optionally show raw metrics side by side clearly marked *not comparable*.
- The comparison computes signed deltas from stored canonical numeric strings/Decimal, and never recomputes an alternative scoring formula in the UI.
- Live confusion matrices use rows = gold label and columns = predicted label, excluding failed/skipped cases. Display compared count, skipped/failed counts, and label taxonomy. Do not show a confusion matrix for the offline suite: it has no judge predictions.

### FR-05 — Admin API contract

All paths are under `/api/v1/admin`, read-only, authenticated, and bounded. Register static `/evaluations/compare` before dynamic `/evaluations/{evaluation_id}` in FastAPI.

| Method and path | Response responsibility |
|---|---|
| `GET /overview` | Time-bounded run status/error/usage summary and latest evaluation summary |
| `GET /evaluations?limit=20&cursor=...` | Keyset-paginated evaluation history; no case payloads |
| `GET /evaluations/compare?left_id=...&right_id=...` | Compatibility reasons and permitted metric deltas |
| `GET /evaluations/{evaluation_id}` | Evaluation provenance, summary, calibration if applicable |
| `GET /evaluations/{evaluation_id}/cases?category=...&limit=...&cursor=...` | Paginated sanitized cases |
| `GET /diagnostics?from=...&to=...` | Aggregate research statuses, error categories, durations, stale-running count |
| `GET /diagnostics/runs?limit=...&cursor=...` | Optional bounded technical run list, no user content |
| `GET /usage?from=...&to=...` | Durable known counters and coverage; estimates only if persistently supported |

Default diagnostics window: last 7 days; maximum window: 90 days. Default page size: 20, maximum: 50. Use UTC, stable sort with ID tie-breaker, query timeouts/indices where necessary. Bad parameters return `422` or existing consistent error envelope; missing evaluation IDs return `404`. Avoid full-table unbounded scans. A route that is not implemented must not be advertised in the UI.

### FR-06 — Research diagnostics

- Query `research_runs` through aggregate/select-column projections, **not** the owner-facing report service and **not** `research_reports`.
- Count each persisted status exactly as stored (`running`, `completed`, `partial`, `insufficient_evidence`, `failed`, `interrupted`). Define successful/failed grouping explicitly; `insufficient_evidence` is a terminal success status in the existing model, not proof of a good answer.
- Compute duration from `started_at` and `finished_at` only when both are valid; do not treat `updated_at` as completion.
- A stale `running` row means its `started_at` is older than 10 minutes (`STALE_RUNNING_AFTER`). It is last recorded state, not proof of an active process.
- Error categories derive from sanitized `error_code` only. No exception messages, stack traces, questions, report bodies, URLs, or prompt excerpts.
- Avoid expensive cross-store joins: no Qdrant/checkpointer traversal in Admin API.

### FR-07 — Usage and cost coverage

- The persisted `ResearchRun.usage` is `GraphUsage` with exactly `iterations`, `search_calls`, `llm_calls`, `pages_indexed`, and `pages_failed` (for stored successful runs). These are logical graph counts, **not** measured provider tokens, SDK retries, physical billable attempts, or a priced bill. Distinguish these concepts.
- If historical per-operation observations are unavailable, show `cost_estimate = null`, `coverage = unavailable`, and an explanation. Never infer `$0.00` from missing data.
- If a durable, validated cost snapshot already exists for a run, it may be displayed with `currency=USD`, `pricing_version`, `coverage`, `unknown_operations`, and `estimated_usd`; otherwise do not backfill from ephemeral `UsageBuffer` or parse logs as authoritative accounting.
- Basic usage dashboard may aggregate known graph counts and number of runs with/without usage. Clearly label the denominator and missing rows; avoid conflating request counts with token counts.
- A new persistent per-operation usage ledger, invoices, budgets, and user chargeback are stretch/out of scope; introducing them requires a separately approved amendment.

### FR-08 — Web UX

- Use existing Next.js 16 App Router, authenticated shell, React/Tailwind, API client and design tokens from Phase 9.
- Proposed routes: `/admin` overview, `/admin/evaluations`, `/admin/evaluations/[id]`, `/admin/evaluations/compare`, `/admin/diagnostics`, `/admin/usage`. Implementation may consolidate pages while preserving discoverability.
- Explicit loading/empty/error/forbidden/unavailable states. Keyboard-accessible tables, pagination, visible labels and legends for confusion matrices; responsive layout without new global state/UI framework.
- Every metric includes a compact explanation and provenance badge/text: `Frozen fixture regression`, `Informational live judge`, or `Operational aggregate`.
- No buttons that imply evaluations can be launched, reports accessed, or billing verified.

## 6. Non-functional requirements

**Security:** 401/403 at backend, no admin bypass of user-owned APIs, response DTO allowlists, no-store, no raw sensitive fields in logs or telemetry, bounded query windows and pagination, safe CLI import errors.  
**Reliability:** importer atomic and idempotent, schema migration upgrade and rollback tested, no changes to existing evaluation CLI/CI baseline.  
**Performance:** admin list/aggregates use indexed SQL; avoid loading report JSON or Qdrant vectors; no provider calls on admin page load.  
**Maintainability:** separate evaluation scoring, artifact import, persistence repository, comparison service, admin query services and API DTOs.  
**Accuracy:** null/unknown is first-class; do not use fixture scores to claim live production quality.

## 7. Acceptance criteria

1. A verified offline JSON artifact imports once, is visible in history, and remains unchanged after reimport; modified or invalid input produces no partial rows.
2. An opt-in live JSON artifact imports without making any provider call, with recomputed count/matrix consistency and explicit `operator_imported` provenance.
3. Existing committed baseline and offline CLI byte-regression remain unchanged and green.
4. Compatible runs show correct retrieval deltas; incompatible dataset/K/scorer/suite/live-judge configuration pairs show reason codes and **no** quality delta.
5. The live matrix uses the same label orientation and skipped/failed exclusions as `calibration.py`; offline never displays a judge matrix.
6. Admin can inspect sanitized evaluation cases, diagnostics, and available usage; no response contains a private user question, report, PDF/source excerpt, raw prompt, token, or judge rationale.
7. Anonymous requests to **every** admin API path return `401`, standard users `403`; direct requests bypassing UI are covered. Admin cannot use regular research APIs to read another user's data.
8. Diagnostics count all six stored statuses correctly; stale-running is clearly labeled as last recorded, and missing duration/usage stays unknown.
9. Cost UI never displays missing spend as `$0`; coverage and pricing version are shown only when supported by durable data.
10. API unit/integration tests, SQL migration tests, Web tests/build, and existing release smoke pass. Gate A/B/C evidence is recorded; no public deployment is implied.

## 8. Gate 0 resolutions

Resolved on 2026-10-24 in [gate-0.md](gate-0.md) and ADR-010 C1–C6:

- Alembic head is `e3b7d91c4a20`. The Phase 10 revision follows it and does not alter research tables.
- `SCORER_VERSION` is `"1"` and is not added to the CLI JSON artifacts. The dataset hash is the lexicographic five-file manifest. Legacy artifacts store `executed_at = null`.
- The committed live artifact has non-null `rationale` on 13 of 15 grounding rows. Import and the admin API omit it, along with claims, questions, excerpts, and URLs.
- Admin pages extend `apps/web/src/app/(app)` and the existing proxy. `GET /auth/me` already returns `role`.
- Usage responses use the five `GraphUsage` fields. Historical dollar coverage is `unavailable` because no cost snapshot is stored on `research_runs`.

## 9. Exit gate

Phase 10 is complete only when the accepted ADR, migrations, import examples, authorized UI/API, compatibility/metric tests, privacy checks, and operational documentation are demonstrated at Gate C. The Alpha integration/release gate remains a separate milestone.
