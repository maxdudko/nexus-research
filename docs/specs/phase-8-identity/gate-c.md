# Gate C — Hardening and Phase 8 exit

Phase 8 code is in place. This gate stays open until an operator records the two-account demo below. That record is a separate change. It does not tag `v0.2.0`, and it is not approval for a public deployment.

## Automated evidence

- Login: five failures return 429 with `Retry-After` and do not hash a sixth password. The address bucket does the same. After the window moves, a good password succeeds, and success clears only that email's failures.
- Billable routes share 10 requests per minute. The 11th does not construct a planner. A foreign scope is charged, then 404, and does not open Qdrant. The 31st scope mint does not insert a row.
- One active `/stream` per user. A second stream for that user is 429 and does not take the process semaphore. Two users can each hold one stream under the process cap. The per-user slot is released when the process cap itself returns 429.
- Logout during a blocked stream stores `interrupted` on the next heartbeat and does not start another model call. A provider node whose session is already dead returns that same interrupted state and does not call the provider.
- An admin session receives 404 for another user's run. The body does not contain the question.
- `identity.audit` and the research log line omit a password, a session token, a question, and a PDF excerpt.
- Packaged smoke bootstraps `smoke@example.com`, requires 401 `unauthenticated` for anonymous research, and keeps the authenticated SSE checks: `research.started`, at least three heartbeats, client cancel stored as `interrupted`, shutdown during a stream, and history after restart. Authenticated `GET /runs` is 200. The 502 while the API is down stays anonymous.
- CI is unchanged. The fast lane stays on `development`. The full lane already runs `pytest -m "integration and not live"` and `scripts/release-smoke.sh` on pull requests and `master`. No live provider call was added.

The upgrade exercise that CI runs starts at schema `b7e2c91a4f10`, the v0.1.0 head. Smoke runs a fresh `init`. Neither pulls the `v0.1.0` image. The operator sequence of cold-copy, quarantine check, `adopt-scope`, and a cross-account 404 is in [operations](../../release/operations.md).

## Residual risk

- Limits are process memory on the one API process. A restart clears the counters.
- The login address bucket sees the socket address of the API connection. Through the Next.js proxy that is the web container, so the cap is instance-wide. The API ignores `X-Forwarded-For` and `Forwarded`. Trusting either header would let a caller choose a fresh bucket.
- The release stack does not terminate TLS.
- A provider call already in flight can finish and spend its cost. A successful stream commit re-reads the locked session and stores `interrupted` with no report when that session is gone. `/run` has no history row and notices revocation at the next provider node. The remaining window is that one locked transaction.
- Qdrant payloads have no owner field. PostgreSQL rejects a foreign scope before the vector call.
- These controls are not a public-deployment review.

## Two-account checklist

Record the date, the commit, and what you saw. Leave a box open until that step was observed. The paid web-research walk-through is an operator action.

- [x] Second account can log in.
- [x] That account can upload a text PDF.
- [x] That account can run web research and reach a terminal status.
- [x] Stopping the stream stores the run as `interrupted`.
- [x] History after a refresh shows that account's own run.
- [x] The other account receives 404 for that run and does not see the question.
- [x] Logout ends the session.

Mark Phase 8 done in the roadmap only after every box above is filled in from an observed run.
