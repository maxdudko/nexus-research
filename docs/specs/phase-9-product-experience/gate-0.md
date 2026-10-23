# Phase 9 Gate 0 — Architecture and UX contracts

**Status:** Approved  
**Date:** 2026-10-23  
**Branch:** `development` at `53aa78a207a06e0ac570e16aa011b51e92f1e443`  
**Normative contract:** [`ADR-009`](../../adr/ADR-009.md) is **Accepted**, including clarifications C1–C8. This file is the review record. It does not change application code.

Gate 0 closes spec questions 1–6. Implementation may start at Step 1. This approval does not tag `v0.2.0`, close Phase 8, or authorize a public deployment.

## Baseline verification

`HEAD` is the spec baseline. The working tree differed from that commit only by the untracked Phase 9 spec, plan, and ADR. The claims below were read from the implementation and, where noted, observed in the running dev server.

### Phase 8 contracts still in force

| Claim | Result |
|---|---|
| `GET /auth/me` is anonymous-401 and `Cache-Control: no-store` on `/api/v1/auth/*`. Login, logout, and `me` exist. Logout revokes the presented session. | Confirmed in `api/v1/auth.py` and `api/csrf.py`. Anonymous `GET /api/v1/auth/me` through the dev proxy returned 401 `unauthenticated`. |
| CSRF allows only `nexus_session` and `nexus_csrf`, checks `Origin`, and does not buffer the body. The proxy forwards those cookies and `Set-Cookie` only. | Confirmed in `api/csrf.py` and `apps/web/src/lib/proxy.ts`. |
| `POST /research/scopes` mints an owned id. The client cannot supply one. | Confirmed in `api/v1/research.py` `create_research_scope`. |
| Stream and PDF require an owned scope before the graph or extraction. A null `research_id` on `/stream` mints a second scope. Missing and foreign ids are 404. | Confirmed in `api/ownership.py`. PDF extraction starts only after `authorize_pdf`. |
| History and detail join `ResearchScope.owner_user_id`. Admin is not a bypass. List order is `created_at DESC, run_id DESC`, `limit` 1–50, default 20. Report JSON is not on the list. | Confirmed in `research/history.py` and `api/v1/runs.py`. |
| Persisted statuses are `running`, `completed`, `partial`, `insufficient_evidence`, `failed`, `interrupted`. A stored `running` row is returned as stored. | Confirmed in `schemas/history.py`. The run detail page already says execution is not confirmed. |
| Question limits are trimmed 10–2000 characters. PDF cap is 10 MB. Image-only PDFs fail with `empty_document` or `unsupported_media`. The original file is not stored. | Confirmed in `schemas/research.py`, `core/config.py`, `ingestion/pdf.py`, and `ai/errors.py`. |
| Report fields are title, summary, sections, limitations, status, and evidence refs. Citations are not entailment. 429 can send `Retry-After`. | Confirmed in `schemas/report.py` and `api/errors.py`. |
| No UI kit and no global client store. Theme is `localStorage` only. | Confirmed in `apps/web/package.json` and `lib/theme.ts`. |

Automated evidence already on this commit, rechecked while writing this gate:

- `apps/web`: `npm test` — 13 files, 60 tests passed (2026-10-23).
- API integration: `pytest -m "integration and not live"` — 60 passed (2026-10-23).
- `scripts/release-smoke.sh` — all checks passed, including anonymous stream 401, authenticated SSE, client disconnect stored as `interrupted`, and the documented stale-`running` warning after API shutdown.

`docs/roadmap.md` still says Phase 8 Gate C is waiting on the two-account demo. The committed checklist in `docs/specs/phase-8-identity/gate-c.md` is already marked complete. This gate does not edit that roadmap line and does not re-close Phase 8.

### Current product behavior

| Area | What the code does |
|---|---|
| Routes | `/` is the form, progress, and history together. `/login` and `/research/[runId]` exist. There is no `/history` or `/research/new`. No `middleware.ts`. |
| Session UX | `AuthGate` returns `null` until `/auth/me` settles. 401 and a thrown fetch both `router.replace("/login")`. Any other status, including 502, renders the workspace. |
| Identity | Email and logout exist only inside `LoginForm` after a successful submit in that instance. The workspace has no account control. Logout ignores the response and still navigates to `/login`. |
| Scope | `useResearchStream` calls `createResearchScope()` on mount and again on retry. A rejected mint is unhandled; `researchId` stays `"pending"` and Start stays disabled. The form prints `Draft {uuid}`. |
| Submit | Order is upload-if-file, then POST SSE, using the mount-minted id. `canStart` allows `idle` and `upload_failed` only. Two clicks in one turn can both pass `canStart` because `stateRef` updates after render, and the second call replaces `abortRef` without aborting the first request. |
| Stream | Reducer phases include client-only `stalled` (45s) and `disconnected`. Stop aborts and dispatches `interrupted` without reading the persisted run. Unmount increments the generation and aborts. There is no leave prompt and no `beforeunload` handler. Terminal SSE fetches the run and then navigates. EOF without a terminal frame reconciles and will not mark success from EOF alone. |
| History | One list, cursor pagination, dedupe by `run_id`. A failed older page keeps loaded rows. Status `running` is labeled "Running". |
| Report | Plain text, not HTML. `CitationLink` allows only `http:` and `https:`. A PDF page is a label plus "The uploaded file is not stored". Failed and interrupted runs render "No report is stored". |
| PDF control | Filename and size are shown. Replace is another file input change. There is no remove control. The hint does not say that a scan can fail or that the file cannot be downloaded later. |

### UI observed on the dev server

The Next.js dev server already running at `http://127.0.0.1:3000` (Next.js 16.4.2) was opened on 2026-10-23. The stored theme was dark and was restored after the check. No invited account was used.

- `/login` at 1280×800 and at 320×720 shows the title, the operator sign-in sentence, email, password, and Log in. The theme button is named "Switch to light theme". At 320 CSS px, `scrollWidth` equals `clientWidth` (320). The icon sits on the title row because the root layout pins it to the top-right of the `max-w-2xl` column.
- Anonymous `/` first paints only that theme button. After `GET /api/v1/auth/me` returns 401, the client replaces the URL with `/login`.
- Removing the `dark` class switches to light. A contrast audit of both themes remains Step 6 work.
- The signed-in workspace was not screenshotted.

## Decisions

These match ADR-009 D1–D8. C1–C8 in the ADR are the binding clarifications for the spec's Gate 0 questions.

| # | Decision |
|---|---|
| 1 | `/` is the dashboard. Research moves to `/research/new`. `/history` is the paginated list. Detail stays `/research/[runId]`. |
| 2 | Authenticated UI lives in one App Router group. A client boundary calls `/auth/me`. Private children render only on 200. 401 goes to login. Any other failure stays on an unavailable view with retry. No second auth library and no Next.js middleware auth. |
| 3 | Scope mint happens once, on explicit valid submit, before PDF and before SSE. The stream body always includes that id. Route unmount aborts the request. |
| 4 | In-app navigation away from an active attempt asks the user to confirm. There is no `beforeunload` promise. Stop does not navigate to the report. |
| 5 | No new endpoint, field, or migration. Dashboard uses `limit=5`. History uses `limit=20`. Stale `running` is copy, not a live flag. |
| 6 | Keep Tailwind tokens and `max-w-2xl`. Header navigation, with a Menu disclosure at narrow widths. Theme toggle leaves the root overlay and sits in each header. |

## Route and state matrix

| Route | Fetches on entry | Must not |
|---|---|---|
| `/login` | `GET /auth/me` to detect an existing session | Render another account's research. Honor an off-site `next`. |
| `/` | `GET /auth/me`, then `GET /research/runs?limit=5` | Mint a scope, open a stream, or mount the research reducer. |
| `/history` | `GET /auth/me`, then `GET /research/runs?limit=20` | Mint a scope or filter more than the loaded pages. |
| `/research/new` | `GET /auth/me` only, until submit | `POST /research/scopes` on mount. Persist the question or `File`. |
| `/research/[runId]` | `GET /auth/me`, then `GET /research/runs/{runId}` | Treat stored `running` as a live stream. Invent a report. |

Submission states on `/research/new`, in order: validate → `creating_scope` → optional PDF upload → `streaming` / `stalled` → terminal or `disconnected`. `scope_failed` and `upload_failed` stop the chain. A new attempt mints a new scope and, if a `File` is still in memory, indexes it again.

Client-only phases remain `stalled` and `disconnected`. They are not written to PostgreSQL.

## API compatibility

| Need | Existing contract | Phase 9 action |
|---|---|---|
| Identity in the header | `GET /auth/me` returns `id`, `email`, `role` | Read it. Do not store it as a token. |
| Logout | `POST /auth/logout` returns `{status: "ok"}` or an error | Treat only 2xx as revocation success. |
| Recent runs | `GET /research/runs?limit=&cursor=` | `limit=5` on the dashboard, `limit=20` on history. |
| Stale running | List has `created_at`. Detail has `updated_at`. No `is_live`. | Copy only. Do not add a field. |
| Scope then PDF then stream | Three existing POSTs | Call them in that order. Always send `research_id` on the stream. |
| Rate limit | 429 body plus optional `Retry-After` | Show the header. Do not auto-retry. |
| Citations | `url` and `page_number` on `EvidenceRef` | Keep `CitationLink`. No download URL. |
| Private GET cache | Auth responses are `no-store`. Run GETs do not set it. | Pass `cache: "no-store"` from the web client. No API change. |

## Component reuse

| Existing | Phase 9 use |
|---|---|
| `app/layout.tsx` | Document shell and theme init script. Remove the absolute theme overlay. |
| `ThemeToggle` | Header on `/login` and in the authenticated shell. |
| `AuthGate` | Becomes the authenticated layout's session boundary, with visible loading and unavailable states. |
| `LoginForm` | Credentials and login errors. Logout moves to the shell. |
| `ResearchForm`, `PdfUpload`, `ResearchProgress` | `/research/new` only. |
| `useResearchStream`, `research-session.ts`, `sse.ts` | Same reducer and parser. Add scope-mint states. Do not start them from the dashboard. |
| `ResearchHistory` | Shared run summary for the dashboard preview and the history page. |
| `ResearchReportView`, `CitationLink`, `lib/report.ts` | Detail page. Add a plain-text citation caveat. |
| `lib/api.ts`, `lib/errors.ts`, `lib/proxy.ts`, `lib/csrf.ts` | Unchanged proxy and CSRF. Client fetches for private data use `cache: "no-store"`. Surface `Retry-After` on 429. |

`use-research-stream.test.tsx` currently expects one scope POST on mount. Step 3 replaces that expectation. It is not a contract to preserve.

## Security regression checklist

Later gates re-check these. None are waived by the new routes.

- Anonymous calls to scope, stream, PDF, history, and detail stay 401 and do not call a provider.
- A foreign or unknown run id stays a generic 404. The UI does not say whether the id exists.
- Admin still cannot read another user's run.
- Logout and account switch drop in-memory research and refetch owner-scoped lists.
- CSRF header and the cookie allowlist stay as they are for POST SSE and multipart PDF.
- The client never puts the session token, question, report, or PDF bytes in `localStorage` or `sessionStorage`. Theme may stay in `localStorage`.
- No scope POST from `/`, `/history`, or an idle `/research/new`.
- Stop, unmount, and disconnect do not report `completed` unless the persisted run says so.

## Implementation boundary

Step 1 may add the authenticated layout, navigation, and session states. It does not change FastAPI, the proxy allowlist, the research reducer semantics, or the report schema. The first suggested change is the shell around the existing pages, with the research form still working until Step 3 moves it.
