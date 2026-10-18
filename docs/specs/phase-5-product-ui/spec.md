# Phase 5 — Product UI: Specification

**Status:** Proposed  
**Project:** Nexus Research  
**Baseline:** `development`, reviewed 2026-10-17 (Phases 1–4 implemented)  
**Decision record:** `docs/adr/ADR-005.md`  
**Implementation:** `docs/specs/phase-5-product-ui/plan.md`

## 1. Purpose

Turn the Phase 4 request-scoped LangGraph research API into a demonstrable, single-user Next.js product: submit a question, optionally index text PDFs into a research scope, observe live progress over SSE, and revisit persisted structured reports with verifiable source references.

**Fixed decisions:** execution is **request-scoped** (no background worker, queue, automatic resume or execution after disconnect guarantee); research history uses **first-class PostgreSQL domain tables**, not checkpoint-table queries. Existing LangGraph PostgreSQL checkpoints remain internal execution state.

## 2. Current repository baseline and reuse

- `apps/api/src/nexus/api/v1/research.py`: `POST /api/v1/research/run` waits for `ResearchRunner.run(question, research_id)` and returns `ResearchRunResponse`; `/plan` and `/discover` remain.
- `apps/api/src/nexus/research/runner.py`: creates `research_id` if absent, mints fresh `run_id`, invokes `graph.ainvoke` with `run_config(thread_id=run_id)` and a deadline; cancellation does not resume in background.
- `apps/api/src/nexus/research/graph.py`: sequential `plan → discover → ingest → retrieve → evaluate → [discover | synthesize | fail]`, with bounded iterations.
- `apps/api/src/nexus/research/state.py`: versioned JSON-safe `ResearchState`; never expose it wholesale to browser.
- `apps/api/src/nexus/research/checkpoint.py`: shared `AsyncPostgresSaver` pool; setup is an explicit command, not request migration.
- `apps/api/src/nexus/schemas/report.py`: `ResearchReport`, `ReportSection`, `EvidenceRef` with validated evidence IDs, URL/page provenance and statuses `completed`, `partial`, `insufficient_evidence`.
- `apps/api/src/nexus/api/v1/evidence.py`: multipart `POST /api/v1/evidence/pdf` with `research_id` and `file`; `POST /web`, `/search` remain. PDFs are text-only, max 10 MiB/100 pages by default; original bytes are **not persisted**.
- `apps/api/src/nexus/main.py`: FastAPI lifespan creates SQLAlchemy engine/session factory and lazy shared checkpoint runtime; use existing resources.
- `apps/api/src/nexus/core/config.py`: limits include 180s graph deadline, five web pages, two iterations and 12 passages by default.
- `docker-compose.yml`: PostgreSQL 18 and Qdrant only; `apps/api/pyproject.toml` targets Python 3.14.
- `docs/roadmap.md`: Phase 5 promises minimal Next.js workspace, PDF upload, SSE, research history, reports and navigable citations. No existing frontend is assumed.

**Non-goal:** redesign Phase 1–4 services, implement authentication or expose research IDs as authorization boundaries.

## 3. User journeys

### J1 — New research
1. User opens home and enters a question (10–2000 trimmed characters, same as backend).
2. Client creates a UUID `research_id` before any optional PDF upload; it remains stable for the session's research scope.
3. User starts research. UI opens a **single POST streaming request** and shows stage events and elapsed time.
4. On terminal `completed`/`partial`/`insufficient_evidence`, UI displays the saved structured report; the user can navigate citations.
5. On failed/interrupted/disconnected execution, UI shows a recoverable error and links to history. Never imply the run is still executing after connection loss.

### J2 — Research with PDF
1. User selects a text PDF, sees local file name/size, and uploads it **before** starting the run to `POST /api/v1/evidence/pdf` with the same `research_id`.
2. Server validates the existing PDF limits; frontend validation is advisory only. UI shows upload/indexing success or error.
3. Run uses the same `research_id`, allowing Phase 4 retrieval to see accepted PDF evidence. **No guarantee that any specific PDF is cited.**
4. Citations for PDFs show source label (when available), page number and evidence ID. Because original PDF bytes are not stored, do not render a broken document-viewer link. Opening the actual PDF requires user-provided local file context or future storage.

### J3 — History and revisiting
1. Home shows recent runs sorted by `created_at DESC, run_id DESC`, paginated.
2. User opens `/research/[runId]` and sees question, status, timestamps, usage and report if terminal-successful.
3. A refreshed browser reconstructs the page via **domain API**, without querying LangGraph checkpoint tables.
4. Failed/interrupted runs remain visible with safe error codes and no invented report.

## 4. Scope and exclusions

**In:** Next.js App Router + TypeScript + Tailwind; simple accessible responsive UI; typed API client; streaming SSE progress; text-PDF upload; run/report domain persistence; list/detail history; structured report rendering; URL citations and PDF page references; retry-as-new-run; tests; documentation.

**Out:** user accounts, multi-tenancy, billing, server-side PDF file storage, PDF viewer, exports, collaborative work, queued jobs, automatic resume, server push after disconnect, WebSockets, advanced agent observability and quality evaluation (Phase 6).

## 5. Backend domain model

Use existing SQLAlchemy Base and Alembic conventions; do not reuse checkpoint tables.

### `research_runs`
- `run_id UUID PRIMARY KEY` — also LangGraph `thread_id`.
- `research_id UUID NOT NULL` — evidence scope, **not** authorization.
- `question TEXT NOT NULL` — trimmed, length validated at API boundary.
- `status TEXT NOT NULL` — `running | completed | partial | insufficient_evidence | failed | interrupted`.
- `error_code TEXT NULL` — safe, stable machine code, not raw exception/provider content.
- `warnings JSONB NOT NULL DEFAULT '[]'` — bounded, sanitized warning codes/strings.
- `usage JSONB NULL` — validated `GraphUsage` at terminal success, optionally last known snapshot.
- `created_at TIMESTAMPTZ NOT NULL`, `started_at TIMESTAMPTZ NULL`, `finished_at TIMESTAMPTZ NULL`, `updated_at TIMESTAMPTZ NOT NULL` (UTC).
- indexes `(created_at DESC, run_id DESC)`, `(research_id, created_at DESC)`; check constraints on status/terminal timestamps as practical.

### `research_reports`
- `run_id UUID PRIMARY KEY REFERENCES research_runs(run_id) ON DELETE CASCADE`.
- `report JSONB NOT NULL` validated against `ResearchReport` on write and read.
- `created_at TIMESTAMPTZ NOT NULL`.

**Transaction semantics:** allocate IDs and insert `running` row **before** graph starts. On graph success, atomically commit terminal run status + report in one DB transaction. On managed errors, persist terminal failed/interrupted status and safe error code in a separate transaction. A report may only exist for `completed`, `partial` or `insufficient_evidence`; failed/interrupted have no report. Never mark completed before report commit. Commit failures must surface a terminal SSE error, not a false success. Use unique `run_id` to make finalization idempotent; reject contradictory terminal updates. On crash or lost client, a `running` row can remain stale; show `unknown/interrupted` UX for stale rows based on documented policy, **without falsely rewriting the DB or claiming the process is active**. A later reconciliation job is out of scope.

**Persistence safety:** do not persist raw PDF bytes, HTML, API secrets or arbitrary provider error text. Reports may include model-generated content and source URLs; render as untrusted text.

## 6. API contract

Retain existing `POST /api/v1/research/run` unchanged for API compatibility and tests.

### `POST /api/v1/research/stream`
- JSON request: same `ResearchRunRequest` `{ "question": string, "research_id": UUID | null }`.
- `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`; disable proxy buffering in deployment.
- One request owns one run. Server allocates IDs and persists the run **before** sending `research.started`.
- No separate detached job is started. On client disconnect, stop streaming and cancel the graph task according to FastAPI/ASGI behavior; cancellation cleanup is best effort with bounded time.
- All frames follow SSE grammar: `event: <name>\ndata: <single-line JSON>\n\n`. Emit periodic comment heartbeats `: ping\n\n` during inactivity (e.g. every 10–15s). Heartbeats are not progress.
- Stream events must be derived from LangGraph `astream(..., stream_mode="updates")` or a tested equivalent; use a **shared** runner lifecycle so `/run` and `/stream` do not fork budgeting, checkpointing or report semantics.
- Each progress event corresponds to a **completed graph node update**. No per-URL ingestion progress is promised without custom node events.
- Server sends exactly one terminal event when connection remains open: `research.completed`, `research.failed` or `research.interrupted`; no additional data frames after terminal. In transport failure, delivery cannot be guaranteed.

#### Public events, version 1

```text
event: research.started
data: {"version":1,"run_id":"UUID","research_id":"UUID","status":"running"}

event: research.progress
data: {"version":1,"run_id":"UUID","stage":"planning|discovery|ingestion|retrieval|evaluation|synthesis","iteration":0,"message":"...","counts":{"pages_indexed":0,"search_calls":0}}

event: research.completed
data: {"version":1,"run_id":"UUID","research_id":"UUID","status":"completed|partial|insufficient_evidence"}

event: research.failed
data: {"version":1,"run_id":"UUID","code":"safe_error_code"}

event: research.interrupted
data: {"version":1,"run_id":"UUID","code":"interrupted|deadline_exceeded"}
```

- `message` is a server-authored stage label, **not** LLM text. Counts are bounded and allowlisted.
- Event payloads must not include `ResearchState`, prompt, API keys, raw chunks, URLs or internal exception messages.
- Completion event is sent **after** successful report/domain commit; client then fetches `GET /api/v1/research/runs/{run_id}`.
- On error before stream headers (validation, unavailable dependency), existing JSON error envelope may be used; after headers, send SSE terminal failure if possible.
- Enforce one terminal event in a state-machine mapper; test chunk fragmentation, disconnect, no-heartbeat starvation and serialization.

### `GET /api/v1/research/runs`
- Query: `limit` default 20, max 50; `cursor` opaque keyset cursor (timestamp + UUID, validated/encoded server-side).
- Response: `{ "items": [RunSummary...], "next_cursor": string | null }`; stable ordering; do not return full reports in list.

### `GET /api/v1/research/runs/{run_id}`
- Response: `{ "run": RunDetail, "report": ResearchReport | null }`; unknown UUID => 404; no provider secrets or checkpoint internals.
- `run` includes IDs, question, status, timestamps, safe error code, warnings and optional usage.

### Existing PDF upload
- `POST /api/v1/evidence/pdf` remains multipart `research_id: UUID`, `file: UploadFile`; use returned `IngestionResponse` and show errors safely.
- For Phase 5, sequential PDF upload before run is supported. **Do not** imply atomic PDF+run transaction.

## 7. Streaming execution and cancellation

- Refactor `ResearchRunner` minimally to share a private execution primitive for invoke and stream, with server-minted IDs supplied by a run coordinator. Keep `run()` behavior and signature backward compatible.
- The coordinator owns: insert domain run → graph execution → terminal domain write → terminal SSE event. Checkpoint remains independent.
- Stream via `graph.astream(..., stream_mode="updates")` and obtain final authoritative state (last graph output or `aget_state`), validate via `validate_state`, then reuse `_response`/report validation; **never infer success solely from the last progress event**.
- `run_id` must be generated **once**, passed unchanged to graph `thread_id`, SSE events and domain tables.
- Respect existing `graph_deadline_seconds` + grace, recursion limits and budgets; no second parallel graph invocation to obtain final result.
- Client disconnect/cancellation: cancel graph task, best-effort bounded persistence of `interrupted` with independent DB session, avoid swallowing `CancelledError`, and close Qdrant client/session. If cancellation interrupts cleanup or process crashes, row may remain `running` and must be represented honestly.
- Distinguish deadline, user abort, provider failure and DB/checkpoint failure. No automatic retries of the same thread; a user retry creates a **new** run with explicit UI wording.
- Do not open one SQLAlchemy transaction across the graph or SSE lifetime.
- `request.is_disconnected()` alone is not a complete cancellation contract; test against actual ASGI streaming behavior.

## 8. Frontend requirements

**Location:** `apps/web` (new Next.js App Router application). Prefer Node LTS, TypeScript strict, Tailwind, a small accessible component layer; avoid introducing a state management framework solely for this MVP. Use `fetch` + tested SSE parser for POST streams; browser `EventSource` cannot POST a JSON body.

**Routes:** `/` (new research + recent history), `/research/[runId]` (report or status). Workspace may be a state on home during active stream; no third route required.

**Components:** `ResearchForm`, `PdfUpload`, `ResearchProgress`, `ResearchHistory`, `ResearchReport`, `CitationLink`, `ErrorState`, `LoadingState`.

**UX:** prevent duplicate submit, display upload/indexing separately from graph progress, show stage labels and iteration counts, preserve run ID once started, show non-deceptive stalled/disconnected state, refresh report from domain API, keyboard accessibility, loading skeletons and mobile layout. Render report text safely (no raw HTML injection); external links `rel="noopener noreferrer"`, validate URL scheme; PDF citations show page label without broken URL.

**Network:** prefer Next.js same-origin `/api/*` rewrite to FastAPI for local/dev deployment, with correct streaming/no buffering and multipart passthrough. Never place OpenAI/Tavily secrets in `NEXT_PUBLIC_*`; only server FastAPI knows provider keys. If rewrite streaming behavior proves unsuitable, use a documented same-origin proxy route with byte streaming and abort propagation, not buffering `await response.text()`.

**SSE client:** incremental UTF-8 decoding, handle `\r\n` and `\n`, multi-line `data:` fields, fragmented chunks, comments, unknown events, malformed JSON and stream EOF; terminal event is authoritative only after domain commit. AbortController cancels local request; no automatic reconnect/replay since server does not support `Last-Event-ID` or detached execution.

## 9. Security and privacy

- **Single-user local demo only**. `research_id` and `run_id` are not access controls. No public multi-user deployment without authentication, authorization and rate limiting. Do not claim UUID secrecy is sufficient.
- SSRF protections remain in existing Phase 3 web ingestion; UI must not bypass backend URL validation.
- PDF backend limits/signature checks remain authoritative. Limit concurrent runs and uploads per process for demo resource safety (configurable; return safe 429/503 on saturation).
- Never log PDF contents, raw prompts, secret headers or complete provider errors; use structured IDs/stages.
- Do not interpolate LLM content into HTML; treat report title/summary/sections as untrusted.
- Sanitize outbound URL protocols; no `javascript:`, `data:` or file links. PDF provenance without persisted bytes is a label, not a downloadable asset.

## 10. Non-functional acceptance criteria

- Same-origin UI + FastAPI works locally; no new queue/worker services.
- Fresh question produces ordered `started → progress* → completed` events and a persisted report retrievable after refresh.
- User-provided `research_id` remains stable across PDF ingestion and run; the returned `run_id` is used everywhere.
- Terminal failed/interrupted runs appear in history without a report; a crash may leave stale `running` status, documented in UI.
- Stream disconnect does not intentionally spawn detached work; no promise of completion after disconnect.
- Progress events are allowlisted, bounded, JSON-valid, and do not leak state or source content.
- Citation IDs resolve to server-attached source metadata; URL citations are clickable; PDF citations show page numbers without pretending bytes are stored.
- Existing `/run`, `/plan`, `/discover`, `/evidence/*` API tests continue to pass.
- Unit tests for SSE parser/event mapper, integration tests for persistence and streaming cancellation, frontend tests for history/report/upload, plus manual E2E demo.
- Quality gate: Ruff, mypy, pytest, Alembic upgrade; frontend typecheck, lint, tests, build; no paid provider calls in CI.

## 11. Risks and explicit trade-offs

1. **Disconnect:** request-scoped execution is not durable; persisted history is not evidence of continued execution.
2. **Graph stream:** node-level events may be sparse during long ingestion; show activity indicator and elapsed time, not fake per-page progress.
3. **Partial persistence:** checkpoint and domain tables are separate writes; handle failures and reconciliation explicitly.
4. **PDF UX:** citations cannot open a previously uploaded PDF without storing bytes; defer viewer/storage.
5. **History privacy:** without auth, list/detail endpoints are unsafe on a public server; restrict deployment.
6. **Proxy buffering:** can destroy perceived streaming; include actual streaming smoke test.

## 12. Definition of Done

A developer can start PostgreSQL/Qdrant, FastAPI and Next.js, upload a text PDF into a research scope, submit a question, see truthful live graph-stage progress, receive a validated report, follow source URLs or PDF page labels, refresh and find the run in domain history, and observe safe failure/disconnect behavior. All without workers, automatic resume, auth, PDF storage or a change to Phase 4's logical budgets.
