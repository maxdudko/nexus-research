# Phase 9 — Product Experience Specification

**Project:** Nexus Research  
**Target:** Alpha `v0.2.0` — Phase 9  
**Status:** Gate 0 accepted 2026-10-23 — implementation may start at Step 1  
**Date:** 2026-10-23  
**Baseline:** `development` at `53aa78a207a06e0ac570e16aa011b51e92f1e443` (inspected 2026-10-23)  
**Related:** [Alpha vision](../alpha-v0.2.0-vision.md), [roadmap](../../roadmap.md), [ADR-008](../../adr/ADR-008.md), [ADR-009](../../adr/ADR-009.md), [implementation plan](plan.md), [Gate 0 record](gate-0.md)

## 1. Objective

Evolve the existing authenticated research UI into a coherent, responsive application that an invited non-developer can use without engineering instructions. The primary journey is **sign in → understand the workspace → start web/PDF research → follow honest progress → inspect a cited report → find it later**.

This is a **product experience phase**, not a rewrite of the research engine, a public SaaS launch, or a new research execution model. Preserve Phase 8 authorization and ownership, Phase 5 request-scoped SSE, report semantics, and MVP evaluation behavior.

### Success statement

An invited user can complete the core journey, recognize partial/failed/interrupted results, revisit their own run after a refresh, and recover from expected API errors without needing developer instructions. The UI must not imply that a disconnected request continues in the background or that citations prove factual correctness.

## 2. Verified starting point

The following is derived from the inspected `development` code; proposed architecture below is a decision, not a claim about current behavior.

| Area | Existing implementation | Phase 9 opportunity |
|---|---|---|
| Routes | `/` combines research form, progress, history; `/login` and `/research/[runId]` exist | Distinct dashboard, new research, history, detail; coherent shell |
| Auth | `AuthGate` calls `/auth/me`; login form owns login and logout UI | Reusable session-aware app shell, reliable expired-session UX |
| Research | `ResearchWorkspace`, `ResearchForm`, `PdfUpload`, `ResearchProgress` | Improve composition and feedback; retain behavior |
| Stream | `useResearchStream`, `research-session.ts` reducer, `sse.ts` parser | Preserve tested lifecycle; clarify interruption and navigation |
| History | Owner-scoped `GET /research/runs`, cursor pagination, list component | Dashboard recent subset; dedicated full history route |
| Report | Owner-scoped `GET /research/runs/{run_id}`, `ResearchReportView`, `CitationLink` | Reading hierarchy, source context, limitations, navigation |
| Proxy | Next.js `/api/v1/[...path]` proxy forwards allowlisted cookies/headers | Keep session, CSRF, no-store, streaming and abort semantics |
| Scope | `useResearchStream` mints scope on component mount and retry | Mint lazily for an actual submission; do not consume quota on page view |
| UI stack | Next.js 16 App Router, React 19, Tailwind 4, Vitest/Testing Library | Reuse stack and tokens; no mandatory UI library |

### Inherited constraints

- `POST /api/v1/research/scopes` creates an owned scope; PDF upload requires that scope before starting a run.
- A run is request-scoped. There is no background worker, automatic resume, or reattachment to an SSE stream after navigation/disconnect.
- The backend persists run history and reports, not original PDF files. An indexed PDF cannot be reopened or re-downloaded from this app.
- A `running` row after a crash may be stale. A history/detail view cannot infer a live process solely from the stored status.
- SSE event stages are `planning`, `discovery`, `ingestion`, `retrieval`, `evaluation`, `synthesis`; event payloads contain only bounded public progress data.
- Terminal run statuses are `completed`, `partial`, `insufficient_evidence`, `failed`, `interrupted`. `disconnected` and `stalled` are **client UI states**, not persisted backend run statuses.
- Reports expose `title`, `summary`, `sections`, `limitations`, `status`, and evidence references; a citation may refer to a URL or PDF page, not necessarily a publicly accessible document.
- Authentication uses session cookies and CSRF protection. Admin identity does not bypass research ownership.

## 3. Users and jobs to be done

**Primary persona:** invited researcher (technical or non-technical) who wants to answer a question with cited evidence, possibly supplemented by a text PDF.

**Secondary persona:** returning researcher reviewing a prior run, including partial or interrupted results.

**Operator:** creates accounts and supports private Alpha access. A rich admin dashboard belongs to Phase 10.

### User stories

- **US-01:** As an invited user, I can sign in and immediately understand how to begin.
- **US-02:** As a user with no runs, I see an actionable empty state rather than a blank history list.
- **US-03:** As a user, I can start research with a valid question, optionally attaching a supported PDF, with clear validation and indexing feedback.
- **US-04:** As a user, I can tell whether research is actively streaming, delayed, completed, partial, insufficient, failed, interrupted, or disconnected.
- **US-05:** As a user, I can stop an active request and understand that stopping is not pausing or resuming.
- **US-06:** As a user, I can read a report and inspect the source references and limitations without confusing them with verified truth.
- **US-07:** As a returning user, I can find and reopen my own previous runs, including runs without reports.
- **US-08:** As a user whose session expires, I am returned to login without seeing another account's cached research.
- **US-09:** As a keyboard/screen-reader user, I can navigate, submit, stop, read status, and open citations.

## 4. Scope

### Core (P0)

1. Responsive authenticated application shell and consistent navigation.
2. Dashboard with primary new-research CTA and recent owner-scoped runs.
3. Dedicated new-research workflow, including optional PDF and accurate SSE lifecycle.
4. Dedicated history route with existing cursor-based pagination.
5. Improved report detail with readable structure, citations, limitations and honest status handling.
6. Reusable authentication/session UX (identity, logout, expired session) without weakening Phase 8.
7. Consistent loading, empty, validation, error and retry states; keyboard and mobile usability.
8. Automated tests and documented manual usability walkthrough.

### Polish (P1, time-boxed after core gates)

- Onboarding hints and sample **generic** research questions (not automatic paid requests).
- Better responsive density, typography, theme contrast and reduced-motion behavior.
- Contextual help about PDF support, citations, provider costs, request interruption and known limitations.

### Out of scope

- Organizations, sharing, folders/projects/tags, saved prompt templates, advanced search indexing, deletion/renaming, billing, subscriptions, notifications or email onboarding.
- Background jobs, durable drafts, auto-resume, stream reattachment, live status polling or persistent upload storage.
- PDF preview/download or OCR; full text search across reports; export and collaboration.
- A Product & Market Intelligence-specific dashboard or competitive monitoring workflow.
- Phase 10 admin/evaluation UI, new AI providers, graph redesign or evaluation metric changes.

Do not add API endpoints or database migrations solely to support speculative UI features. Any needed backend contract change must be justified and reviewed separately.

## 5. Information architecture and routes

| Route | Purpose | Access | Primary content |
|---|---|---|---|
| `/login` | Sign in | Anonymous or already authenticated | Credentials, errors, safe redirect |
| `/` | Dashboard | Authenticated | Welcome, New research CTA, recent owner-owned runs |
| `/research/new` | Start/monitor research | Authenticated | Question, optional PDF, streaming progress, stop/retry |
| `/history` | Browse research runs | Authenticated | Paginated list, statuses, links to detail |
| `/research/[runId]` | View one persisted run | Authenticated + owner | Status, report or absence, citations, limitations |

The shell exposes Dashboard, New research, History, account identity/logout and theme toggle. On narrow screens, navigation must be usable without covering the form or report. Avoid adding an `/account` route solely for email/logout; a menu is sufficient. Route names were accepted in ADR-009 at Gate 0.

**Navigation semantics:** visiting `/` or `/history` never starts research or mints a scope. Opening `/research/new` alone must not consume scope quota. Deep-linking to `/research/[runId]` must work after refresh; unauthorized/nonexistent IDs remain indistinguishable (`404` from API).

## 6. Functional requirements

### FR-01 — App shell and identity

1. A single reusable authenticated layout contains navigation, main landmark, theme control, signed-in email and logout.
2. Route protection uses the existing `/auth/me` contract. Loading and API-unavailable states must be visible, not an indefinitely blank `AuthGate`.
3. `401` from a private endpoint transitions to a login-required state or redirects to `/login`. `403` and `404` are not treated as successful access. Do not display raw server errors.
4. Logout uses the existing CSRF-protected endpoint, clears user-specific client state, and navigates to login. Do not claim logout succeeded if revocation failed; provide a safe retry/error path.
5. Authenticated responses and report data must not be cached across accounts by the browser app or Next.js. Do not add persistent client caches for private data.
6. Theme choice may remain a local UI preference; do not treat it as a user profile feature.

### FR-02 — Dashboard and history

1. Dashboard shows a clear New research action and up to the existing page-size limit of recent runs, fetched from owner-scoped `GET /research/runs`.
2. The full history route supports existing cursor pagination (`limit` and `next_cursor`), correct ordering, deduplication and independent loading/error/empty states.
3. Every run item shows a truncated question, persisted status and timestamp, with a keyboard-accessible detail link.
4. `running` is labeled **last recorded status**, not proof of an active stream. Do not invent a live-progress indicator for reopened history.
5. Avoid claiming a global search/filter exists. A visual client-side filter, if added, must clearly state that it filters only loaded items; full-text search is not part of Phase 9.
6. A history refresh after a completed run must not duplicate entries or expose previous-account data.

### FR-03 — New research and PDF

1. Validate question length against the current API contract: trimmed **10–2000 characters**.
2. Optional PDF remains limited to supported text PDFs and **10 MB** (existing client limit); clearly state that scanned/image PDFs may not work and original files are not stored for later download.
3. Display selected filename, size, ability to remove/replace before execution, upload/indexing state and safe failure/retry messages.
4. Do not create a research scope on mount. On valid submission, create an owned scope **once**, then upload the optional PDF, then open the SSE stream. If scope mint fails, show an actionable error without opening the provider stream.
5. Prevent duplicate submissions while scope creation, upload or stream is active. Retry-as-new-run must allocate a new scope and must never silently reuse another user's scope.
6. Preserve the selected question on safe retry. Be explicit about PDF behavior: a newly created scope requires re-indexing an attached PDF; no persisted original upload is available.
7. Surface `429` rate/concurrency limits and `Retry-After` when available, without automatically issuing billable retries.

### FR-04 — Progress, interruption and recovery

1. Reuse the existing typed reducer and SSE parser. Show real stage messages only when received; do not fabricate percent-complete, token counts or node activity.
2. Show the last received stage, elapsed time, heartbeat age and a clear stalled state after the existing 45-second client threshold. A stalled stream is not automatically failed.
3. **Stop** aborts the active request; it is not pause/resume. The UI must not promise immediate server-side cancellation or zero provider cost.
4. A route change/unmount while streaming can abort the request. Provide an understandable leave-warning where feasible; never suggest the run will continue independently. Browser unload and mobile process termination cannot be guaranteed to trigger a warning or cleanup.
5. On terminal SSE, reconcile with the persisted run detail and navigate to `/research/[runId]` when available. A transport EOF without a terminal event must attempt existing detail reconciliation; do not claim success from a disconnected stream.
6. If a run ID is unavailable after disconnection, show a safe unconfirmed outcome. If stored status is `running`, explain that execution is not confirmed and suggest checking history later, without automatic resume.
7. Retain one active stream per user and process-level concurrency limits enforced by the API. Do not build a client queue.

### FR-05 — Report and evidence

1. Show question, persisted status, timestamps and safe error/warning details. Show report only if one is stored.
2. Render title, summary, sections, citations and limitations with clear hierarchy and long-text wrapping. Do not insert model text as unsanitized HTML.
3. Preserve evidence IDs and URL/PDF page references. A PDF citation without a resolvable file URL must not appear as a downloadable link.
4. Distinguish `completed`, `partial`, `insufficient_evidence`, `failed`, `interrupted`, and stale/unknown `running` UX. Do not render an invented report for failed/interrupted runs.
5. Explain that citations identify supporting source references and that report limitations/uncertainty still apply. No factual-accuracy guarantee.
6. Report pages are directly navigable by run ID, with separate loading, unauthorized/session-expired, not-found and service-unavailable states.

### FR-06 — Accessibility and responsive behavior

1. Semantic navigation, header, main, headings and labeled form controls; keyboard-visible focus; no keyboard traps in menus/drawers.
2. Status changes use appropriate `role=status`/`aria-live` and errors use `role=alert` without announcing every SSE heartbeat.
3. Stop, retry, upload and navigation work by keyboard. Maintain visible labels for icon-only actions.
4. Mobile layout at a **320 CSS px** viewport without horizontal page overflow; desktop reading widths remain comfortable.
5. Respect reduced-motion preference for nonessential animation. Check contrast for both themes and status indicators; color is not the only signal.
6. Prefer progressive enhancement and existing CSS tokens over a new design-system dependency.

## 7. API and state contracts

**Reuse unchanged where possible:**

- `GET /api/v1/auth/me`, `POST /api/v1/auth/logout`.
- `POST /api/v1/research/scopes`.
- `POST /api/v1/evidence/pdf` (multipart `research_id`, `file`).
- `POST /api/v1/research/stream` (JSON `question`, `research_id`; `text/event-stream`).
- `GET /api/v1/research/runs?limit=...&cursor=...` and `GET /api/v1/research/runs/{run_id}`.

The existing `apps/web/src/lib/types.ts` is a hand-written mirror of Pydantic schemas. Keep it aligned with the backend and test response validation. Do not introduce a new backend field such as `is_live`, `progress_percent`, `original_pdf_url`, or `can_resume` without an actual backend contract.

**State separation:**

- **Authentication state:** current identity and expiry; never persisted in browser storage as a token.
- **Research draft/session state:** question, optional in-memory `File`, created scope ID, SSE lifecycle; local to `/research/new` and discarded on leaving.
- **Persisted run state:** loaded from owner-scoped history/detail APIs, not inferred from a previous client stream after refresh.
- **UI state:** navigation/theme/dialogs; must not become an alternative source of truth for ownership or run status.

## 8. Failure and recovery matrix

| Scenario | Expected UX | Forbidden behavior |
|---|---|---|
| Anonymous deep link | Login-required redirect/state | Private data flash |
| API unavailable on `/auth/me` | Explicit retry/unavailable view | Silent infinite blank screen |
| Session expired during request | Login-required state, no stale private view | Keep showing previous account's report |
| Scope mint fails / 429 | Explain and allow deliberate retry | Start PDF/stream anyway |
| PDF invalid/oversized/unsupported | Actionable error, preserve question | Treat file as indexed |
| PDF indexing fails | Stop before stream, allow retry | Silent web-only fallback |
| Stream stalled | Last-known activity and stop | Fake percent or auto-complete |
| User stops/leaves stream | Explain interruption, reconcile if possible | Claim pause/resume or background work |
| Stream disconnects | Reconcile by run ID if known | Mark completed from EOF alone |
| Persisted `running` after refresh | Last-known/unknown explanation | Claim currently active |
| Report missing/failed/interrupted | Honest no-report state | Fabricated summary |
| Foreign/nonexistent run ID | Generic not-found view | Existence leak |
| Cursor page error | Keep already loaded items, retry | Duplicate or clear valid results |

## 9. Security and privacy invariants

- Keep ADR-008 session cookies, CSRF, Origin and proxy allowlists intact, including POST SSE and multipart PDF.
- Only the API authorizes resources; UI route guards and disabled buttons are usability controls, **not** authorization.
- Do not leak user questions, report bodies, PDF excerpts, tokens or source passages to analytics, logs or error telemetry by default.
- On logout/account switch, discard private client state and refetch owner-scoped history. Do not cache report JSON in localStorage or service workers.
- External citations are untrusted URLs. Preserve existing safe-link handling; use appropriate `rel` attributes for new-tab links and do not create unsafe `javascript:` navigation.
- Do not add new provider calls to page load, onboarding or automated visual tests.

## 10. Verification and acceptance

### Automated checks

- Existing API authorization, isolation, SSE, CSRF and report tests remain green.
- Web component tests cover shell, auth expiry, logout failure/success, dashboard empty/error/recent, history pagination/deduplication, form/PDF validation, scope mint once, SSE terminal/disconnect, report statuses, citations and not-found handling.
- Verify no scope POST is issued on Dashboard, History or idle `/research/new` mount.
- Test navigation/unmount abort semantics and retry-as-new-run; use deterministic mocked streams, not live providers.
- Test mobile navigation keyboard interactions and accessible status/error announcements where practical.
- Run `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` for `apps/web`; retain API integration tests and packaged smoke on PR/release gates.

### Manual two-account UX walkthrough

Record date, commit, browser/viewport and evidence; use an approved private environment:

1. Invited account A logs in and reaches Dashboard; account B sees only B's history.
2. Account A starts a question-only web research run and sees meaningful progress, then a persisted report.
3. Account A attaches a text PDF to a **new** research scope and completes a run; citation behavior is clear.
4. Account A interrupts a separate run; UI and persisted history do not falsely claim success.
5. Refresh and deep-link to completed, partial/insufficient, failed and interrupted examples (use fixtures where practical).
6. Open an unknown/foreign run ID: generic not-found without data leak.
7. Logout, revisit private routes, log in as B, and confirm no A data flashes or remains cached.
8. Repeat key navigation and form actions at 320 px mobile width and keyboard-only; inspect both themes.

### Phase 9 Definition of Done

- Core routes, shell, research, history and report are coherent and accessible; new users can complete the intended flow without developer guidance.
- No regression to authenticated ownership, CSRF, SSE cancellation or report provenance.
- No implied background execution, resume, original-PDF retrieval or guaranteed factual accuracy.
- Automated gates and recorded manual walkthrough pass; known limitations documented.
- ADR-009 is Accepted, the implementation plan gates are closed with evidence, and the roadmap is updated **only after verification**.

## 11. Gate 0 resolutions

Accepted 2026-10-23. The review record is [gate-0.md](gate-0.md). Normative detail is ADR-009 D1–D8 and clarifications C1–C8.

1. `/` is the dashboard. Research is `/research/new`.
2. One authenticated route group loads `/auth/me` in a client boundary. 200 renders the page, 401 goes to login, and any other failure is an unavailable view. No second auth framework.
3. A scope is minted once on submit, then the optional PDF is indexed, then the stream opens with that `research_id`. Unmount aborts the request.
4. In-app navigation away from an active attempt asks for confirmation. Browser unload is not intercepted.
5. No backend change. The list's missing `updated_at` and the absent original PDF are handled in copy.
6. Existing tokens, a `max-w-2xl` column, and a header Menu disclosure at narrow widths. No component library.
