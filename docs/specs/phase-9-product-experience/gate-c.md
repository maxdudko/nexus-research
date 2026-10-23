# Phase 9 Gate C — Report experience and polish

**Status:** Not approved  
**Date:** 2026-10-23  
**Branch:** `development`. The fixes in this commit follow `5093f35d794e0deaf246133e6775878617a3aa10`.  
**Normative contract:** [ADR-009](../../adr/ADR-009.md). This file records what was observed. It does not tag `v0.2.0`, close Phase 9, or approve a public deployment.

The Phase 9 roadmap status is unchanged. Gate C is not accepted: a second history page was not available, and disconnect, insufficient evidence, failed, and stale-running were not produced in this invited session.

## Observed

From `apps/web` on 2026-10-23, after the scope, history, logout, and dialog fixes:

- `npm run lint` passed.
- `npm run typecheck` passed.
- `npm test` passed: 19 files, 109 tests.
- `npm run build` passed.

From `apps/api`, with the local Postgres and Qdrant already running:

- `uv run pytest -m "integration and not live"` passed: 60 tests.
- `scripts/release-smoke.sh` rebuilt `nexus-api:0.1.0` and `nexus-web:0.1.0` from this tree and passed, including anonymous stream 401, authenticated SSE, client disconnect stored as `interrupted`, and the documented stale-`running` warning after API shutdown. The smoke stack uses a model stub and does not produce a report.

Regression coverage added with these fixes:

- A failed PDF upload keeps its scope for another Start of the same file. Replacing the file, clearing it, or choosing an oversized PDF drops that scope id. The next start mints a new scope, and a cleared file is not uploaded.
- A failed history refresh retries `listRuns(limit)` with no cursor and does not offer Older runs. A failed older page retries that cursor and does not refetch the first page.
- Logout during active research asks first. Stay does not revoke the session or abort the request. Confirm aborts the request, then `POST /api/v1/auth/logout` with the CSRF header. A 500 stays on the page with the signed-in email. A 2xx goes to `/login`.
- The leave dialog is `aria-modal`, labelled, focused on Stay, and portaled so the rest of the page is inert. Escape stays and returns focus to the opener.

Invited walkthrough on the local dev server at `http://127.0.0.1:3000`, signed in through the UI. The browser viewport was about 898 CSS px, then 320 CSS px. Both themes were used. No screen reader was used.

- Account `gate-c-a@example.com` reached Dashboard. The empty state said there were no runs, and the page did not start research.
- A question-only run finished as **Partial**, with warnings, web citations, and the sentence that a citation does not prove the report is correct. Refreshing `/research/e12ba97a-de2e-4b02-bec0-3594d5d25a7f` showed the same report.
- A second run attached `sample.pdf` (830 bytes, one page of text). The form showed the indexing limit and that the original file is not kept. The stored run is **Completed**. Its PDF reference reads “PDF · page 1. The uploaded file is not stored, so this page cannot be opened.” It is not a download link.
- Stop on a third run left the page at **Interrupted** and did not open a report. History lists that row as Interrupted. Confirming logout during a fourth active run also stored **Interrupted**, then opened `/login`.
- `/history` after logout redirected to `/login?next=%2Fhistory` and did not show the previous questions.
- Account `gate-c-b@example.com` opened History and saw “No research runs yet.” Opening account A’s report id showed “The research run was not found.” and did not show A’s question or report.
- At 320 CSS px, login, Dashboard, New research, and History had `clientWidth` and `scrollWidth` of 320. Menu opened, focused Dashboard, and closed on Escape back to Menu. The signed-in email, Log out, and the question field remained usable.
- While a run was active, History opened “Leave this research?” with focus on Stay and the main content inert. Escape stayed on `/research/new` and returned focus to History. Logout opened “Log out?” with the same behavior until confirmed.

Stored statuses for this session: partial, completed, interrupted, interrupted. There is no second history page, so Older runs was not shown.

## Checklist

- [x] Core routes and the app shell work at desktop and 320 CSS px.
- [x] An invited user can start a web run and read a persisted cited report.
- [x] An invited user can attach a text PDF and understand indexing and citation limits.
- [ ] Stop, disconnect, partial, insufficient, failed, and stale-running states are truthful in a manual run. Stop, partial, and interrupted were. Disconnect, insufficient evidence, failed, and a stale-running row were not opened in this invited session. Packaged smoke still reports the stale-running caveat on its own stack.
- [ ] History pagination and a direct report link work after refresh for a signed-in user. The direct report link worked after refresh. This account had one page of runs, so Older runs was not exercised.
- [x] Account A cannot see account B’s research through the UI in this review. Logout and account switch clear private state in a manual session.
- [x] Keyboard navigation, labels, focus, and the leave and logout dialogs passed this manual review. No separate screen reader was used. The dialog Tab cycle is covered by the component test.
- [x] Web lint, typecheck, test, and build pass, and API integration plus packaged smoke pass on this tree.
- [ ] Known limitations and this evidence are recorded, and a reviewer has accepted Gate C. The evidence is recorded. A reviewer has not accepted Gate C.

## Not claimed

- Phase 9 is not complete.
- `v0.2.0` is not tagged, and this record is not approval for a public deployment.
- Abandoned scopes from a failed or replaced upload are left in place, as ADR-009 requires. They are not deleted on leaving the page.
- Browser unload still cannot warn or guarantee cancellation. The new-research page says so, and `beforeunload` is not registered.
