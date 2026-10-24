# Phase 10 Gate C — Diagnostics, usage, and release evidence

**Status:** Evidence recorded. Reviewer acceptance is not signed.  
**Date:** 2026-10-24  
**Branch:** `development` at `dc0ade856f307ebe6fc24a9ae5c70c34d36d8b77`, plus the uncommitted Phase 10 working tree.  
**Normative contract:** [ADR-010](../../adr/ADR-010.md). This file records the commands that were run. It does not approve Phase 9 Gate C, mark the roadmap Phase 10 row done, tag `v0.2.0`, or authorize a public deployment.

`docs/evaluation/baseline.json` was not modified. Its SHA-256 remains `1b872057d429dd664de33fc7efc76bb18be1f82f2672e50f273fe14ac76fabc7` (8,513 bytes).

## Commands

From `apps/api` on 2026-10-24:

| Command | Result |
|---|---|
| `uv run ruff check .` | Passed. |
| `uv run mypy src` | Passed. 118 source files. |
| `uv run pytest -m "not integration and not live"` | 506 passed, 82 deselected. |
| `uv run pytest -m "integration and not live"` | 73 passed, 515 deselected. Includes scope downgrade, the admin privacy canary, and the contradictory live-import rejection. |
| `uv run python -m nexus.evaluation --suite offline --output /tmp/nexus-eval-offline.json` then `diff -q` against `docs/evaluation/baseline.json` | No difference. The temporary file has the same SHA-256 as the baseline. |

From `apps/web`:

| Command | Result |
|---|---|
| `npm run lint` | Passed. |
| `npm run typecheck` | Passed. |
| `npm test` | 22 files, 125 tests passed. |

`scripts/release-smoke.sh` was run locally with Docker. It exited 0. The smoke log includes `all release smoke checks passed`. Init left the schema at `f6c1a8d04e27`. The run recorded the existing warning that an API shutdown during a stream can leave a row `running`. That warning is the documented stale-running caveat. The smoke stack uses a model stub and does not call a paid provider.

## Review fixes recorded with this run

- A live grounding case is stored only when `outcome`, `predicted_label`, `error_code`, `model`, and `prompt_version` agree. An `ok` row needs a prediction, a model, and a prompt, and no error code. A `skipped` row has an error code and none of those three. An `error` row has an error code, no prediction, and model and prompt together or not at all. Setting `error_code` on an otherwise valid `ok` row in the published live artifact returns the fixed live-mismatch sentence and leaves `evaluation_runs` and `evaluation_case_results` empty.
- Duration counts, the summed known interval, and the longest known interval come from one SQL statement. A missing or reversed finish stays unknown. The 1.500s and 2.500s fixture still reports `4.000` and `2.500`. Sub-millisecond fractions are quantized on those two intervals.
- Usage validation still loads each `usage` object inside the 90-day window, because `GraphUsage` (`extra=forbid`) is the validator. That scan is the Alpha bound. `cost_estimate` stays null, `coverage` stays `unavailable`, and `pricing_version` stays null.
- An incompatible comparison keeps both stored recall values, lists `dataset_mismatch`, and returns `deltas: null`. The compare page shows both sides and the reason list, and it does not render the delta table.

## Query plan

On the development database, `EXPLAIN` for the diagnostics run list is a bitmap index scan on the existing `ix_research_runs_created_at_run_id`. The index condition is `created_at >= now() - interval '7 days' AND created_at < now()`, and the sort key is `created_at DESC, run_id DESC`. No additional index was added. The index definition is `btree (created_at DESC, run_id DESC)`.

Loading an admin page does not construct a Qdrant client or a chat client. The diagnostics integration test patches both constructors and records no calls.

## Privacy canary

The diagnostics integration test plants a question, a report sentence, a source `display_name`, an invalid usage object, and a rationale string. Those strings are absent from `GET /overview`, `GET /diagnostics`, `GET /diagnostics/runs`, and `GET /usage`. An admin `GET /api/v1/research/runs/{id}` for that quarantine-owned run is 404, and the body does not contain the question. Anonymous requests are 401, a non-admin is 403, an admin is 200, and `Cache-Control` is `no-store`.

A positive `llm_calls` still returns `cost_estimate: null`, `coverage: unavailable`, and `pricing_version: null`. The two-account isolation test passed in the same integration run.

## Checklist

- [x] Diagnostics, usage, and overview are read-only admin GETs. The window, stale-running rule, null duration, and unknown cost match ADR-010 C4–C5.
- [x] The privacy canary and the admin caller's 404 on another account's run were observed on the disposable database.
- [x] Ruff, mypy, unit tests, the integration suite, the offline baseline diff, web lint, typecheck, web tests, and local packaged smoke passed on this tree.
- [ ] A reviewer has accepted Gate C. The evidence above is recorded. A reviewer has not accepted this gate.

## Not claimed

- Phase 9 Gate C stays **Not approved**.
- The roadmap Phase 10 row stays **Proposed**.
- `v0.2.0` is not tagged. This record is not approval for public hosting.
- Evaluation `executed_at` stays null. The import clock is not an execution time.
- Usage cost stays unknown. It is not recorded as zero.
- The usage scan inside the 90-day window is still process-side validation. It is not a SQL aggregate.
