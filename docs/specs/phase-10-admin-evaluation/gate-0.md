# Phase 10 Gate 0 — Admin and evaluation contracts

**Status:** Approved  
**Date:** 2026-10-24  
**Branch:** `development` at `9fb4f096606f59f61f47713c8f8f7f0b71e7c4c5`  
**Normative contract:** [`ADR-010`](../../adr/ADR-010.md) is **Accepted**, including clarifications C1–C6. This file is the review record. It does not change application code.

Gate 0 closes the technical checks in the spec. The four product choices were already approved on 2026-10-24. Implementation may start at Step 1. This approval does not close Phase 9 Gate C, tag `v0.2.0`, or authorize a public deployment.

## Baseline verification

`HEAD` is `9fb4f096606f59f61f47713c8f8f7f0b71e7c4c5` (`Merge branch 'master' into development`). Phase 8 (`1a02dc2`, Identity & Multi-User) and Phase 9 (`f217a69`, Product Experience) are ancestors. The working tree differed from that commit only by the untracked Phase 10 spec, plan, and ADR. Alembic has one head: `e3b7d91c4a20` (`research_scopes`), which revises `c8e4a1b27d05`. The chain is `79d871cb4dad` → `6f509f423779` → `b7e2c91a4f10` → `c8e4a1b27d05` → `e3b7d91c4a20`.

The claims below were read from the implementation. The offline and live checks were executed from `apps/api` on 2026-10-24.

### Phase 8 contracts still in force

| Claim | Result |
|---|---|
| `require_admin` depends on `get_current_user` and allows only `role == "admin"`. It does not query `ResearchScope`. | Confirmed in `api/deps.py`. `users.role` is constrained to `user` or `admin`. |
| A missing session is 401 `unauthenticated`. A non-admin principal is 403 `forbidden` with message `The request was rejected.` | The role split is the unit test `test_require_admin_is_explicit`. Anonymous rejection is `get_current_user`. |
| History and detail join `ResearchScope.owner_user_id`. Run routes depend on `get_current_user`, not `require_admin`. | Confirmed in `research/history.py` and `api/v1/runs.py`. |
| Persisted statuses are `running`, `completed`, `partial`, `insufficient_evidence`, `failed`, `interrupted`. Success statuses are the middle three. `insufficient_evidence` is a stored terminal success, not a quality score. | Confirmed in `models/run.py`. |
| `ResearchRun.question` is required text. `usage` is optional JSONB. Reports live on `research_reports.report`. | Confirmed in `models/run.py`. `finalize_success` writes `GraphUsage` only. Failures write no report and leave `usage` null. |
| `GraphUsage` is `iterations`, `search_calls`, `llm_calls`, `pages_indexed`, `pages_failed`. | Confirmed in `schemas/report.py` and `research/state.py` `graph_usage`. |
| Dollar estimates exist only on the request-local `UsageBuffer` and the `research.usage` log line. They are not a column. | Confirmed in `observability/usage.py` and `observability/costs.py`. |

`docs/specs/phase-8-identity/gate-c.md` has the two-account checklist marked complete. This gate does not re-close Phase 8.

### Phase 9 shell this phase extends

| Claim | Result |
|---|---|
| Authenticated routes are the `(app)` group: `/`, `/history`, `/research/new`, `/research/[runId]`. The layout renders `AuthenticatedLayout`. | Confirmed in `apps/web/src/app/(app)/`. |
| The session boundary calls `GET /auth/me`. Identity already includes `id`, `email`, and `role`. The header receives email and user id, and its nav is Dashboard, New research, and History. | Confirmed in `lib/identity.ts`, `session-boundary.tsx`, and `components/shell/app-header.tsx`. |
| The frame is `max-w-2xl`. Private fetches use `cache: "no-store"`. A 401 on history or run detail uses `loginHref(pathname)`. | Confirmed in `lib/api.ts` and the history and detail pages. |
| `isAllowlistedPath` allows only `/`, `/history`, `/research/new`, and one `/research/{uuid}` path. | Confirmed in `lib/return-path.ts`. |
| The proxy forwards `content-type`, `accept`, `origin`, and `x-csrf-token`, plus cookies `nexus_session` and `nexus_csrf`. It forwards `cache-control`. The route is `/api/v1/[...path]`. | Confirmed in `lib/proxy.ts` and `app/api/v1/[...path]/route.ts`. |
| `Cache-Control: no-store` is applied by middleware only for paths under `/api/v1/auth`. CSRF applies to unsafe methods and does not buffer the body. | Confirmed in `api/csrf.py`. |

`docs/specs/phase-9-product-experience/gate-c.md` is still **Not approved**. The shell and owner-scoped routes are on this commit. Phase 10 uses that shell. This gate does not accept Phase 9 Gate C.

### Evaluation baseline

| Check | Result |
|---|---|
| Constants | `DATASET_VERSION = "2026-10-19.2"`, `REPORT_VERSION = "1"`, `K = 5`, `PROMPT_VERSION = "grounding-judge-2"`, `JUDGE_TEMPERATURE = 0.0`. There is no `scorer_version` field. |
| CLI | `python -m nexus.evaluation --suite offline\|live --output PATH`, with `--fixtures` and `--max-cases` (default 12). Live still requires `NEXUS_RUN_LIVE_AI=1`, prints the pricing warning, and refuses a missing API key. |
| CI | `.github/workflows/ci.yml` runs the offline suite and `diff`s it with `docs/evaluation/baseline.json`. |
| Offline recompute | `run_offline(load_dataset())` serialized by `dump_report` equals `baseline.json` byte for byte. Citation and grounding expectations match, so the imported outcome is `passed`. Retrieval does not decide pass/fail. |
| Baseline shape | Keys are `suite`, `dataset_version`, `k`, `report_version`, `retrieval`, `citations`, `grounding`. No `executed_at`, commit, or dataset fingerprint. Aggregate: Recall@5 `0.833333`, Precision@5 `0.240000`, MRR@5 `0.705556`, 15 defined cases, 1 undefined. Counts: 16 retrieval, 10 citation, 15 grounding. |
| Baseline bytes | 8,513 bytes. SHA-256 `1b872057d429dd664de33fc7efc76bb18be1f82f2672e50f273fe14ac76fabc7`. `docs/evaluation/outputs/nexus-eval-offline.json` is the same bytes. |
| Live artifact | `docs/evaluation/outputs/nexus-eval-live.json` validates as `LiveReport`. Model `gpt-4o-mini`, prompt `grounding-judge-2`, temperature `0.0`, pricing version `2026-10-19`. 15,982 bytes. SHA-256 `d385285ea60fed52792319affbbe2391bf8371fd1ec5df110d273f9212bf3bfd`. |
| Live consistency | `judge_counts` and `calibrate` match the file. Retrieval and citations match the offline recompute. Gold labels match the fixtures. Counts: attempted 13, succeeded 13, failed 0, skipped 2, disagreed 2. Agreement `0.846154` on 11 of 13. Excluded from the matrix: `g09-foreign` and `g10-unknown`, both `skipped` with a null prediction. `calibration.matrix["unsupported"]["contradictory"]` is 2. |
| Rationale | 13 of 15 live grounding rows have a non-null `rationale`. The schema allows it (`max_length=500`). The fixture files also contain `text`, `url`, `question`, and `claim`. |

Current fixture fingerprint, using the algorithm in C1: `be4bf734127c3b152430855b133f9e6751f7a9955ed8f52b8471cc4a0a0fce68`. Per-file SHA-256 values:

| File | SHA-256 |
|---|---|
| `citation_cases.json` | `ac040cf4aab8b00cd1c33d07cc9b5637c33b883a3fbac723ae67109871d211a5` |
| `corpus.json` | `78dfc246c6824229323f318805685c817229cbc6630a9b700e135d8f7c60bc64` |
| `grounding_cases.json` | `ecf7018f60626e7cfa28e4cb5a6cbb226fc494331dcf09648d81d423393dc511` |
| `queries.json` | `072f1241da622782b5e18f59c3d9262c69cac4d5d5da4a4da329560eac2f4d9e` |
| `retrieval_outputs.json` | `2fbc036ef9df2408a28e3b51fea951fff8b704fb91f1bd4391da1269fe4d48ae` |

That digest identifies today's files. The importer computes it. A later fixture change produces a different digest and is a different dataset.

## Decisions

These match ADR-010 D1–D7. C1–C6 in the ADR are binding.

| # | Decision |
|---|---|
| 1 | CLI execution stays `python -m nexus.evaluation`. Import is `python -m nexus.admin.evaluation_import` and uses the identity CLI's database settings. |
| 2 | `SCORER_VERSION` is `"1"` and stays out of the evaluation JSON artifacts. |
| 3 | Dataset identity is the lexicographic raw-byte manifest above. Artifact identity is the raw file SHA-256, capped at 2 MiB. |
| 4 | `executed_at` stays null. `code_revision` is optional and `operator_asserted`. `--committed-baseline` is the only way to set the baseline flag. |
| 5 | Comparison, pagination, windows, and the 404 message are the C4 contract. |
| 6 | Admin responses are allowlisted DTOs. Cost coverage for research runs is `unavailable` until a durable estimate exists. The Phase 9 shell gains an admin link and an extended login return allowlist. |

## Import metadata

| Input | Stored value |
|---|---|
| Offline artifact whose recomputed report matches | `suite=offline`, `provenance_level=offline_recomputed`, `outcome=passed` or `failed` from citation and grounding expectation checks |
| Live artifact whose counts, matrix, retrieval, citations, and gold labels match | `suite=live`, `provenance_level=operator_imported`, `outcome=informational` |
| `--committed-baseline` and bytes equal to the committed baseline, after the offline check passes | `is_committed_baseline=true` |
| Same flags omitted, or bytes differ | Import rejected when the flag is set and the bytes differ. Without the flag, `is_committed_baseline=false` |
| `--code-revision` | Lowercase 40-hex SHA and `code_revision_source=operator_asserted` |
| No revision flag | Both revision columns null |
| Any current artifact | `executed_at=null`, `report_version=1`, `scorer_version=1`, `k` from the artifact |
| Reimport of the same bytes and the same derived metadata | Existing id, same `imported_at` |
| Same bytes with a different flag or fixture fingerprint | Conflict. The stored row stays as it was |

`config` for every run:

```json
{"empty_gold": "undefined", "metric_quant": "0.000001"}
```

Live `config` also includes `"label_taxonomy": ["supported", "unsupported", "insufficient_context", "contradictory"]`.

Offline `summary` contains `retrieval` (the five aggregate fields), `citations` (`cases`, `valid`, `invalid`), and `grounding` (`cases`, `structurally_valid`, `structurally_invalid`). Live `summary` adds `counts` and `calibration` from the recomputed models, including the matrix.

Case `result` objects, with `case_id` and `category` stored as columns:

| Category | Keys |
|---|---|
| `retrieval` | `gold_count`, `recall_at_k`, `precision_at_k`, `mrr_at_k` |
| `citation` | `citation_valid`, `expect_citation_valid`, `support_label`, `error_codes` |
| `grounding` | `structural_valid`, `expect_structurally_valid`, `gold_label`, `error_code`, `excerpt_count`, and for live rows also `predicted_label`, `outcome`, `model`, `prompt_version` |

Metric strings match the artifact's six-decimal form or are null. `error_codes` and `error_code` match `^[a-z0-9_]{1,64}$`. Live `expect_structurally_valid` is copied from the fixture after the structure check. The live artifact does not carry that field.

## Sensitive data exclusion

A field is listed even when the importer needs it in memory for a check. "Persist" and "Admin API" mean the value must be absent after that step. Logs and CLI stderr follow the same exclusion.

| Field | Where it exists today | Persist | Admin API |
|---|---|---|---|
| `CorpusChunk.text`, `url` | `corpus.json` | No | No |
| `QueryCase.question` | `queries.json` | No | No |
| `CitationCase.claim` | `citation_cases.json` | No | No |
| `GroundingCase.claim`, excerpts | grounding fixtures and `PreparedClaim` | No | No |
| `GroundingJudgeResult.rationale` | live artifact, 13 rows | No | No |
| Judge `timestamp`, `input_tokens`, `output_tokens`, `usage_source`, `evidence_ids` | live case objects | No | No |
| `ResearchRun.question` | `research_runs` | Unchanged, owner-scoped | No |
| `ResearchRun.research_id`, `warnings` | `research_runs` | Unchanged | No |
| `ResearchReportRecord.report` | `research_reports` | Unchanged | No |
| `owner_user_id`, account email | `research_scopes`, `users` | Unchanged | No |
| Source text, PDF bytes, Qdrant payload, checkpoint state | existing stores | Unchanged | No |
| Session cookie, CSRF token, provider key, database URL | auth and settings | Unchanged | No |
| `ResearchRun.usage` object | success rows, five integers | Unchanged | Aggregated counters only |
| `UsageBuffer`, `estimated_usd` log field | process log | Not a ledger | No |
| `run_id`, `status`, `error_code`, timestamps | `research_runs` | Unchanged | Yes, on diagnostics |
| Evaluation metrics, labels, bounded error codes, versions, digests | artifacts and fixtures | Yes, allowlisted | Yes |

Import and admin logs may record the evaluation id, suite, outcome, and a fixed error code. They omit artifact bodies and exception text from the database driver.

## API and shell contract

All routes are `GET` under `/api/v1/admin`, depend on `require_admin`, and send `Cache-Control: no-store`.

| Path | Contract |
|---|---|
| `/overview` | Default 7-day run summary plus the latest evaluation card |
| `/evaluations` | Keyset on `imported_at DESC, id DESC`. Headline metrics, no cases, no matrix |
| `/evaluations/compare` | Registered before the id route. Query `left_id`, `right_id` |
| `/evaluations/{evaluation_id}` | Provenance, summary, config, and live calibration |
| `/evaluations/{evaluation_id}/cases` | Keyset on `category, case_id`. Optional `category` |
| `/diagnostics` | Six status counts, error-code counts, duration, stale running |
| `/diagnostics/runs` | `run_id`, `status`, `error_code`, timestamps, derived duration |
| `/usage` | Five graph counters, denominators, `coverage=unavailable`, `cost_estimate=null` |

Cursor payload is version 1, URL-safe base64 without padding, using the same alphabet rule as research history. Evaluation cursors carry `imported_at` and `id`. Case cursors carry `category` and `case_id`. Diagnostic run cursors carry `created_at` and `run_id`. A bad cursor, limit, category, or window is 422 `invalid_request` and names the field only.

Duration is `finished_at - started_at` when both timestamps exist and the finish is at or after the start. Otherwise it is unknown. The aggregate reports `known_count`, `unknown_count`, `total_seconds`, and `max_seconds`. Seconds are decimal strings with millisecond precision. A missing duration stays null.

Status counts always include all six stored statuses. The response also groups `recorded_success` (`completed`, `partial`, `insufficient_evidence`), `recorded_failure` (`failed`, `interrupted`), and `recorded_running`. Stale running is a separate count: status `running`, non-null `started_at`, older than 10 minutes, inside the window. A running row with a null `started_at` is `running_without_start`, not stale. The UI calls both last recorded state.

Usage sums are over rows whose `usage` validates as `GraphUsage`. Rows without usage increment `runs_without_usage`. An invalid usage object increments `runs_without_usage` and is not returned. The five counters are logical graph counts. The cost object stays null even when those counters are zero.

Web routes, all inside `(app)`: `/admin`, `/admin/evaluations`, `/admin/evaluations/[id]`, `/admin/evaluations/compare`, `/admin/diagnostics`, `/admin/usage`. Add those exact paths, plus `^/admin/evaluations/{uuid}$`, to `isAllowlistedPath`. `compare` stays an exact path so it is not treated as an id.

The header Admin link renders only for `role === "admin"`. A signed-in non-admin who opens `/admin` sees a forbidden state and the page does not call `/api/v1/admin`. A later 401 uses `loginHref`. A 403 stays on the forbidden state. A network failure or 5xx uses the existing unavailable copy and retry. Admin fetches use `cache: "no-store"` and are kept in memory only.

Provenance badges stay `Frozen fixture regression`, `Informational live judge`, and `Operational aggregate`. The baseline badge is additional copy on a row with `is_committed_baseline`. Unknown `executed_at` is shown as unknown import provenance, not as the import time.

## Test strategy

No test code is added at this gate. Later steps implement these cases.

**Step 1.** Constraints for suite, outcome, provenance, category, digests, revision source, and live-versus-offline nullability. Composite case key, unique artifact hash, cascade delete. Upgrade from `e3b7d91c4a20` with existing research rows leaves those rows unchanged. Downgrade drops only the two new tables. Concurrent duplicate insert returns one row.

**Step 2.** Offline recompute matches `baseline.json`. Live counts and the matrix match `nexus-eval-live.json` with no provider call. Reject an unknown `report_version`, a file over 2 MiB, a forged matrix, a duplicate case, and a dataset hash from a different fixture byte. Reimport returns the same id. A second import with `--code-revision` or `--committed-baseline` flipped is a conflict. Sentinel strings planted in rationale, claim, question, excerpt, and URL are absent from the stored JSON. Rollback leaves zero new rows.

**Step 3.** Anonymous 401, user 403, admin 200, revoked session 401, unknown id 404 with the evaluation message. `compare` is not captured by the UUID route. Comparable offline deltas match `Decimal` subtraction. Incompatible pairs list reason codes and omit deltas. The live matrix uses gold rows and predicted columns and excludes the two skipped cases. Offline detail has no matrix. Responses have no question, report, rationale, or email, and they send `no-store`.

**Step 4.** Admin link hidden for `user` and shown for `admin`. Forbidden page issues no admin request. List, detail, and compare cover a compatible pair, an incompatible pair, and a live matrix. Offline pages do not render a matrix. Pagination and the 320px menu still work. `sessionStorage` and `localStorage` gain no admin payload.

**Step 5.** Fixture rows for all six statuses. Empty window is zeros and null cost. Stale running uses `started_at`, not `updated_at`. Missing finish time leaves duration null. A 91-day window is 422. Usage sums the five fields and keeps `cost_estimate` null when `llm_calls` is a positive number.

**Step 6.** The privacy canary covers a user question, a report sentence, a source excerpt, and a live rationale. The offline CI diff still passes. Release smoke still passes. Two accounts still cannot read each other's research, including an admin account.

## Implementation boundary

Step 1 may add `models/evaluation.py`, export it from `models/__init__.py`, add one Alembic revision after `e3b7d91c4a20`, add the insert/read repository, and add schema and migration tests. The first commit can be the models, revision, and schema tests alone. The evaluation CLI, `baseline.json`, research routes, proxy, and Next.js app stay as they are until later steps.
