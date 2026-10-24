# Phase 10 Gate C — Diagnostics, usage, and release evidence

**Status:** Evidence recorded. Reviewer acceptance is not signed.  
**Date:** 2026-10-24  
**Branch:** `development` at `0eaa3bee791b4761afabab0e361bd67f9303f370`, plus the uncommitted Phase 10 working tree.  
**Normative contract:** [ADR-010](../../adr/ADR-010.md). This file records the commands that were run. It does not approve Phase 9 Gate C, mark the roadmap Phase 10 row done, tag `v0.2.0`, or authorize a public deployment.

`docs/evaluation/baseline.json` was not modified. Its SHA-256 remains `1b872057d429dd664de33fc7efc76bb18be1f82f2672e50f273fe14ac76fabc7` (8,513 bytes).

## Commands

From `apps/api` on 2026-10-24:

| Command | Result |
|---|---|
| `uv run ruff check .` | Passed. |
| `uv run mypy src` | Passed. 118 source files. |
| `uv run pytest -m "not integration and not live"` | 493 passed, 81 deselected. |
| Admin integration modules: `test_admin_diagnostics`, `test_evaluation_schema`, `test_evaluation_store`, `test_evaluation_api`, `test_evaluation_import_db` | 12 passed. |
| `uv run python -m nexus.evaluation --suite offline --output /tmp/nexus-eval-offline.json` then `diff -u` against `docs/evaluation/baseline.json` | No difference. The temporary file has the same SHA-256 as the baseline. |
| `tests/integration/test_ownership_isolation.py` | Passed after local Qdrant was started. The first attempt failed because nothing was listening on `127.0.0.1:6333`. That was the environment, not an ownership regression. |

From `apps/web`:

| Command | Result |
|---|---|
| `npm run lint` | Passed. |
| `npm run typecheck` | Passed. |
| `npm test` | 22 files, 125 tests passed. |

`scripts/release-smoke.sh` was run locally with Docker. It exited 0. The smoke log includes `all release smoke checks passed`. Init left the schema at `f6c1a8d04e27`. The run recorded the existing warning that an API shutdown during a stream can leave a row `running`. That warning is the documented stale-running caveat. The smoke stack uses a model stub and does not call a paid provider.

## Query plan

On the development database, `EXPLAIN` for the diagnostics run list is a bitmap index scan on the existing `ix_research_runs_created_at_run_id`. The index condition is `created_at >= now() - interval '7 days' AND created_at < now()`, and the sort key is `created_at DESC, run_id DESC`. No additional index was added. The index definition is `btree (created_at DESC, run_id DESC)`.

Loading an admin page does not construct a Qdrant client or a chat client. The diagnostics integration test patches both constructors and records no calls.

## Privacy canary

The diagnostics integration test plants a question, a report sentence, a source `display_name`, an invalid usage object, and a rationale string. Those strings are absent from `GET /overview`, `GET /diagnostics`, `GET /diagnostics/runs`, and `GET /usage`. An admin `GET /api/v1/research/runs/{id}` for that quarantine-owned run is 404, and the body does not contain the question. Anonymous requests are 401, a non-admin is 403, an admin is 200, and `Cache-Control` is `no-store`.

A positive `llm_calls` still returns `cost_estimate: null`, `coverage: unavailable`, and `pricing_version: null`.

## Checklist

- [x] Diagnostics, usage, and overview are read-only admin GETs. The window, stale-running rule, null duration, and unknown cost match ADR-010 C4–C5.
- [x] The privacy canary and the admin caller's 404 on another account's run were observed on the disposable database.
- [x] Ruff, mypy, unit tests, admin integration tests, the offline baseline diff, web lint, typecheck, web tests, and local packaged smoke passed on this tree.
- [ ] A reviewer has accepted Gate C. The evidence above is recorded. A reviewer has not accepted this gate.

## Not claimed

- Phase 9 Gate C stays **Not approved**.
- The roadmap Phase 10 row stays **Proposed**.
- `v0.2.0` is not tagged. This record is not approval for public hosting.
- Evaluation `executed_at` stays null. The import clock is not an execution time.
- Usage cost stays unknown. It is not recorded as zero.
