# Phase 8 Gate 0 — Security and migration contracts

**Status:** Approved  
**Date:** 2026-10-22  
**Branch:** `development` at `05ce011305a10e3aec7f4da8cbcb1ad93f403685`  
**Normative contract:** [`ADR-008`](../../adr/ADR-008.md) is **Accepted**. This file is the review record. It does not change application code.

Gate 0 closes spec decisions 1–7. Implementation may start at Step 1. This approval does not invite users, tag `v0.2.0`, or authorize public deployment.

## Baseline verification

`v0.1.0` (`9a0dde06dd5d5df5dce31a353dd120159352e6cb`) is an ancestor of `HEAD`. The spec baseline `7f25fa0e07c1705d6ad77da23f984df80dd3721b` and `HEAD` differ only by the Phase 8 spec, plan, and ADR. The working tree was clean. The claims below were read from the implementation, not taken from the spec text alone.

| Claim | Result |
|---|---|
| `ResearchRun` has `run_id`, `research_id`, and status. `research_id` is a UUID with no foreign key and no `user_id`. `ResearchReportRecord` references `research_runs.run_id` only. | Confirmed in `models/run.py` and Alembic `b7e2c91a4f10`. |
| `Source` identity is `(research_id, content_hash)` for PDF and `(research_id, uri)` for web. No owner. | Confirmed in `models/source.py` and Alembic `6f509f423779`. |
| `list_runs()` is instance-wide keyset pagination. `get_run()` loads by primary key. | Confirmed in `research/history.py`. Neither query has an owner predicate. |
| `/plan`, `/discover`, `/run`, and `/stream` have no principal. `/stream` inserts history. `/run` does not. | Confirmed in `api/v1/research.py` and `research/runner.py`. `runner.run` mints a scope when `research_id` is omitted and never calls `ResearchHistory`. |
| `/pdf`, `/web`, and `/search` accept a client `research_id` with no authorization. | Confirmed in `api/v1/evidence.py`. |
| Qdrant and source lookups filter by `research_id`. That is evidence scoping. | Confirmed in `retrieval/service.py` and `retrieval/vector_store.py`. |
| LangGraph `thread_id` is `run_id`. No public checkpoint route. | Confirmed in `research/graph.py` `run_config`. `api/v1/router.py` mounts only health, research, runs, and evidence. |
| The proxy forwards `content-type` and `accept`, and does not forward cookies or `Set-Cookie`. | Confirmed in `apps/web/src/lib/proxy.ts`. |
| Settings have no auth fields. `NEXUS_STREAM_MAX_CONCURRENT` defaults to 2 and is capped at 8. | Confirmed in `core/config.py`. The semaphore is process-local in `research.py`. |
| The web client mints `research_id` with `crypto.randomUUID()` before PDF upload. | Confirmed in `hooks/use-research-stream.ts`. |
| Release API is one uvicorn process with no `--workers`. Web publishes `127.0.0.1` only. | Confirmed in `apps/api/Dockerfile` and `compose.release.yml`. |
| MVP is unauthenticated and loopback-only. Rollback is a cold volume restore. Alembic downgrade is not a release procedure. | Confirmed in `docs/release/limitations.md` and `docs/release/operations.md`. |
| CI fast lane stays on `development`; integration, images, and smoke run on pull requests and `master`. | Confirmed in `.github/workflows/ci.yml`. |

Alembic head on this branch is `b7e2c91a4f10`. `argon2-cffi` is not yet a dependency. Step 1 adds it. No schema was changed for this gate.

`get_runner`, `get_ingestion`, and `get_retrieval` open a Qdrant client, and `get_runner` can open the checkpointer, as FastAPI dependencies before the handler runs. Step 4 must make those dependencies require an already-resolved principal so a 401 does not open them, take a stream permit, or insert history.

## Decisions

| # | Decision | Record |
|---|---|---|
| 1 | Local Argon2id passwords and opaque PostgreSQL sessions. Managed identity, OAuth, and JWT browser sessions are rejected for Phase 8. | ADR-008 D1–D3 |
| 2 | `ResearchScope` is the only ownership root. `POST /api/v1/research/scopes` mints the id. PDF upload uses that id and does not mint another. | ADR-008 D5–D6 |
| 3 | Pre-Alpha scopes belong to a non-loginable quarantine user until `adopt-scope`. The first admin does not receive them. | ADR-008 D7 |
| 4 | Cookie, CSRF, origin, and proxy rules in the ADR accepted contract. | ADR-008 D3–D4 |
| 5 | One API process. Process-local login throttle and per-user billable quotas in the ADR accepted contract. | ADR-008 D8 |
| 6 | Revocation during SSE cancels through the existing interrupt path at the next heartbeat or provider-node boundary. Residual window is documented below. | ADR-008 accepted contract |
| 7 | Phase 8 ships a minimal `/login` path and account strip. Phase 9 owns the shell. | ADR-008 D9 and accepted contract |

## Threat model

**Assets:** password verifiers, session tokens, the session pepper, provider keys, research questions, reports, PDF text, evidence passages, and quarantined MVP rows.

**Principals:** anonymous caller, invited user, admin, operator with a shell on the host, and the quarantine identity (not a principal that can log in).

**Trust boundary:** browser → Next.js same-origin proxy → FastAPI principal → PostgreSQL authorization → research services → Qdrant, checkpointer, and providers. Every id in a request is an untrusted selector.

| Threat | Accepted mitigation | Residual risk |
|---|---|---|
| Account enumeration and online guessing | Same 401 body for unknown and wrong passwords; dummy Argon2 verify; per-email and per-IP failure caps | Counters die on process restart. Accepted only because the approved deployment is one private process. |
| Stolen session cookie | `HttpOnly`, host-only, `Secure` except the explicit loopback exception; server-side revocation; 12h absolute cap | A same-origin XSS can still call the API. The CSRF cookie is readable by script. Phase 8 does not add a CSP program. |
| Cross-site state change, including PDF and POST SSE | Allowlisted `Origin` plus double-submit `X-CSRF-Token` on unsafe cookie requests. `SameSite=Lax` is extra, not the policy. | Login is Origin-checked and has no CSRF token yet, because no session exists. Login CSRF would sign the browser into an attacker-controlled account; invited accounts and the lack of public signup limit that. |
| IDOR / BOLA on scope, run, report, search, upload, stream | Owner predicate in SQL before pagination and before any provider, Qdrant, or history side effect. Missing and foreign ids share one 404 envelope. Server-minted scope ids only. | Handler-only checks are rejected. Gate B must show this with two real accounts. |
| Scope takeover via a client UUID | No insert of a caller-supplied `research_id`. Reuse is a select of an owned row. | Concurrent create of two server-minted ids is harmless. |
| Admin reads another user's research | `require_admin` does not skip ownership. | A later Phase 10 diagnostic view needs its own access review. |
| First login claims MVP data | Quarantine user cannot authenticate, and adoption is an explicit CLI. | An operator who adopts the wrong scope discloses that scope. The CLI logs the scope id and destination user id so the action is attributable. |
| Session or password in logs, traces, or the browser bundle | Store HMAC only. Proxy allowlists two cookie names. Audit fields exclude secrets and content. | Provider keys remain in `.env.release` and are visible to host operators, unchanged from `v0.1.0`. |
| Database leak of `auth_sessions` | HMAC pepper is not in the database. | Pepper rotation invalidates every session. That is acceptable and not a Phase 8 feature. |
| Billable abuse by an invited user | Per-user request cap, one `/run` and one `/stream` each, existing process-wide stream cap, existing graph budgets | Caps are not a currency budget. Operator keys still pay for allowed calls. |
| Revoked session keeps a live stream | Heartbeat and pre-provider checks cancel into `interrupted`. | An in-flight provider call can finish. Bound is the current graph deadline (default 180s). |
| Proxy smuggles a foreign cookie or drops `Set-Cookie` | Cookie name allowlist on the way in and out; multi-value `Set-Cookie` preserved | Direct browser-to-API access stays off the published port. |
| Migration deletes or reassigns history | Transactional backfill, no Qdrant rewrite, no checkpoint rewrite, downgrade refused after a real owner exists | There is no automatic downgrade once a scope is adopted. Recovery is the pre-upgrade volume restore, which discards writes after the backup. |

Public Internet exposure is out of this threat model. `docs/release/limitations.md` remains in force until a later exposure review.

## Migration, backup, and recovery

Operator sequence for an existing `v0.1.0` dataset:

1. Stop the stack. Cold-copy `postgres_data` and `qdrant_data` together, as in `docs/release/operations.md`. Record the git commit. Keep `.env.release` with the backup, outside the repository.
2. Upgrade with the Phase 8 Alembic revision. Init remains idempotent: a second `upgrade head` is a no-op.
3. Confirm quarantine ownership, unchanged run/report/source counts, and an evidence search that still resolves through existing Qdrant payloads. Do not reindex as part of the upgrade.
4. Invite nobody until Gate C. Adoption of a quarantined scope is a separate, explicit command after the operator has reviewed the scope id.

Fresh install creates the same quarantine user and no scopes. It must not require a backup.

**Failure before the migration commits:** PostgreSQL rolls the revision back. Qdrant and checkpoint bytes are untouched because the revision does not write them. Start the previous image against the same volumes.

**Failure after commit, before any real owner exists:** `downgrade()` may drop only the new identity tables and foreign keys. It must abort when any scope owner is not the quarantine user. The supported operator path is still the volume restore.

**Failure after adoption or after invited users have written data:** restore both volumes from the pre-upgrade backup and check out that commit. Writes after the backup are lost. Do not run the old image against the new schema. `init` never downgrades.

Qdrant is unchanged by a successful migration, but it is still backed up with PostgreSQL so a restore cannot pair a new database with an old collection, or the reverse.

## Authorization matrix

Anonymous callers receive 401 on every route below except health. Authenticated callers receive the uniform 404 for a missing or foreign scope or run. Role denial is 403 and is not used to hide research existence.

| Route | Anonymous | Authenticated use |
|---|---|---|
| `POST /api/v1/auth/login` | Origin check, throttle, 401 on bad credentials | Re-issues the session cookies |
| `POST /api/v1/auth/logout` | 401 | CSRF, revoke current session, clear both cookies |
| `GET /api/v1/auth/me` | 401 | Minimal identity, `Cache-Control: no-store` |
| `POST /api/v1/research/scopes` | 401 | Mint an owned id. No client id. Hourly cap. |
| `POST /api/v1/research/plan` | 401 before the model | Billable quota. No scope required. |
| `POST /api/v1/research/discover` | 401 before search or the model | Billable quota. No scope required. |
| `POST /api/v1/research/run` | 401 before the graph | Owned or newly minted scope. Does not gain history persistence in this phase. |
| `POST /api/v1/research/stream` | 401 before permit, history, or graph | Owned or newly minted scope. CSRF because it is a cookie-authenticated POST. |
| `GET /api/v1/research/runs` | 401 | Owner filter inside the keyset query |
| `GET /api/v1/research/runs/{run_id}` | 401 | Owner join before the report is loaded |
| `POST /api/v1/evidence/pdf` | 401 before the upload is parsed for extraction | Existing owned scope, multipart, CSRF |
| `POST /api/v1/evidence/web` | 401 before fetch | Existing owned scope |
| `POST /api/v1/evidence/search` | 401 before the embedding | Existing owned scope |
| `GET /api/v1/health/live` | 200, no I/O | Unchanged |
| `GET /api/v1/health/ready` | Postgres and Qdrant status only | Stays unauthenticated for the Compose probe |

`/plan` and `/discover` stay billable and authenticated, and they stay free of a research scope, matching the current request schema.

## Measurable acceptance cases

These are the Gate 0 cases later steps must implement. No test code is added at this gate.

**Migration**

- Fresh `upgrade head` twice leaves one quarantine user, zero scopes, and the previous research tables empty.
- A fixture at Alembic `b7e2c91a4f10` with a shared research id, a run-only id, a source-only id, and one report upgrades to one scope per distinct id, all owned by `00000000-0000-4000-8000-000000000001`, with the same run, report, and source rows.
- The upgrade performs no Qdrant upsert and no checkpoint write.
- `adopt-scope` moves one quarantined scope, refuses a second user's scope, refuses the quarantine user as destination, and emits `identity.audit` without the question text.
- `downgrade()` succeeds only while every scope is quarantined, and it leaves runs, reports, and sources in place.

**Sessions**

- Argon2 verify and mismatch, email normalization, duplicate email, disabled login, quarantine login, bootstrap second run, reset revokes all sessions, expired and idle sessions, HMAC stored instead of the raw token.
- Login, logout, and `me` through the Next.js proxy. `Set-Cookie` flags match the environment. A foreign cookie is not forwarded. Logout's clearing `Set-Cookie` reaches the browser.
- Wrong CSRF and wrong Origin change nothing. PDF multipart and POST SSE send the CSRF header and still stream or upload without buffering.
- Auth responses are `no-store`.

**Isolation**

- Each private route in the matrix returns 401 with zero provider calls and zero new rows when anonymous.
- User A receives 404, and no side effect, for user B's scope on run, stream, PDF, web, search, history, and detail.
- History pagination does not reveal B's rows when A manipulates the cursor.
- Two concurrent claims of one existing scope: only the owner succeeds.
- A revoked or disabled session already inside a stream ends as `interrupted` without waiting for the graph deadline when the check runs at the next heartbeat and no provider call is in flight.
- A normal user receives 403 from `require_admin` with no side effect.
- Own-scope PDF deduplication, retrieval by accepted digest, report citations, SSE cancel, and the offline evaluation baseline stay as they are.

Gate B is not met by unit tests or by UUID randomness. It needs two accounts against PostgreSQL and Qdrant.

## Implementation boundary

Step 1 may add `User`, `AuthSession`, the password hasher, the bootstrap CLI, settings, and their tests. It does not change research routes, Qdrant, the proxy, or the UI. The first suggested commit remains Step 1A in the plan.
