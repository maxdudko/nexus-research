# Phase 5 — Product UI: Detailed Implementation Plan

**Status:** Ready for Cursor Plan → Build → Review  
**Baseline:** `development` reviewed 2026-10-17  
**References:** `spec.md`, `docs/adr/ADR-005.md`, Phase 4 `spec.md`/ADR-004  
**Fixed:** request-scoped SSE, PostgreSQL domain history, Next.js App Router, PDF upload before run, no workers/resume.

## Working rules

1. Read existing code before editing. Match Python 3.14, FastAPI, async SQLAlchemy, Alembic, pytest, Ruff/mypy and current error envelope; use established dependency injection and test fixtures.
2. Keep `/api/v1/research/run` backward-compatible and do not bypass `ResearchRunner`, `GraphContext`, `run_config`, existing budget/deadline logic or accepted-evidence filtering.
3. Build in six steps and three review gates. At each gate: tests green, commit, review; fix issues before next group.
4. No paid provider calls in default CI. Use fakes for graph/provider tests; separate opt-in live smoke test.
5. Do not add a queue, automatic resume, multi-user auth, durable PDF storage or unrelated Phase 1–4 refactoring.
6. Proposed filenames below are targets, not assertions that files exist. Inspect repository naming conventions before implementation.

## Group A — Product Backend

### Step 1 — Run/report domain persistence and read API

**Goal:** stable domain history independent of LangGraph checkpoints.

**Inspect first:** `apps/api/src/nexus/infrastructure/database.py`, `apps/api/src/nexus/sources/models.py` or actual model package, `apps/api/alembic/env.py`, latest migrations, `apps/api/src/nexus/schemas/report.py`, `apps/api/src/nexus/api/v1/research.py`, `apps/api/src/nexus/api/v1/router.py`, existing test fixtures.

**Proposed files:**
- New `apps/api/src/nexus/research/models.py` or existing SQLAlchemy models module: `ResearchRun`, `ResearchReportRecord`.
- New `apps/api/src/nexus/research/history.py`: repository/service for create, finalize, interrupt, list and get.
- New `apps/api/src/nexus/schemas/history.py`: Pydantic `RunSummary`, `RunDetail`, `RunListResponse`, `RunDetailResponse`.
- New Alembic revision in `apps/api/alembic/versions/`.
- New `apps/api/src/nexus/api/v1/runs.py` or extend existing research router; include routes at `/api/v1/research/runs` and `/api/v1/research/runs/{run_id}`.
- Tests: `apps/api/tests/test_research_history.py`, `apps/api/tests/integration/test_research_history_db.py` (adapt to actual test layout).

**Implementation:**
1. Define run statuses consistent with `ResearchState.RunStatus`; avoid inventing incompatible `queued` state.
2. Add PostgreSQL tables and indexes exactly as specified; register metadata for Alembic autogenerate. Keep LangGraph saver tables untouched.
3. Repository functions: `create_running(run_id, research_id, question, now)`, `finalize_success(run_id, validated_report, warnings, usage, now)`, `finalize_failure(run_id, safe_error_code, interrupted, now)`, `list_runs(limit,cursor)`, `get_run(run_id)`.
4. Success finalization must update run and insert report **in one transaction**; validate Pydantic report before write. Use compare-and-set/row locking so a second conflicting finalization cannot overwrite a terminal result. Decide whether duplicate identical finalization is a no-op and test it.
5. Failures persist only safe error codes; never store raw provider exceptions. Keep `running` rows visible if a crash occurs before finalization.
6. Use keyset pagination `(created_at,run_id)` descending. Encode cursor as opaque validated value; reject malformed cursors with 422. Return list summaries without report JSON.
7. Build thin FastAPI handlers, use existing session factory and error conventions. Do not open a DB transaction across a graph run.

**Tests / acceptance:**
- Alembic upgrade from clean database; repeated upgrade; rollback of new revision if supported.
- `running → completed/partial/insufficient_evidence` creates one report and correct timestamps.
- `running → failed/interrupted` creates no report and safe error code.
- Report-write failure rolls back status update; never persists `completed` without report.
- Concurrent/duplicate terminal updates cannot create contradictory state.
- List ordering, cursor pagination without duplicates, detail 404, malformed UUID/cursor.
- Domain history works after app/checkpointer recreation; no checkpoint reads in list/detail.

**Exit:** domain storage and GET APIs work with fake data and PostgreSQL integration tests.

### Step 2 — Streaming coordinator, SSE contract and cancellation

**Goal:** add one streaming execution path while preserving Phase 4 semantics.

**Inspect first:** `apps/api/src/nexus/research/runner.py`, `graph.py`, `state.py`, `checkpoint.py`, `nodes/*`, `apps/api/src/nexus/api/v1/research.py`, `apps/api/src/nexus/main.py`, `apps/api/src/nexus/api/errors.py`.

**Proposed files:**
- Modify `apps/api/src/nexus/research/runner.py` to expose shared internal execution and a stream iterator; preserve public `run()`.
- New `apps/api/src/nexus/research/events.py`: typed allowlisted public events + serializer.
- New `apps/api/src/nexus/research/streaming.py`: LangGraph update-to-stage mapper and SSE frame encoder.
- New `apps/api/src/nexus/research/coordinator.py`: domain lifecycle around run execution (or small equivalent integrated service).
- Modify `apps/api/src/nexus/api/v1/research.py`: add `/stream` and dependency wiring.
- Modify `apps/api/src/nexus/core/config.py` only for justified heartbeat/concurrency settings.
- Tests: `test_research_events.py`, `test_research_streaming.py`, `test_research_stream_integration.py`.

**Design checkpoint before coding:**
- Write a sequence diagram and decide how to derive **final state from a single `astream` execution**. LangGraph updates may be deltas, not a complete `ResearchState`; validate final state from graph's persisted snapshot (`aget_state`) or a supported stream output mode **without launching a second run**.
- Avoid reusing `ResearchRunner.run()` from `/stream` if that invokes the graph separately. Extract a shared lifecycle primitive or coordinator.
- Explicitly choose who creates IDs; mint exactly once, persist before emitting started, and pass same `run_id` to graph `thread_id`.
- Distinguish `graph deadline` and `HTTP/proxy deadline`; test hard timeout and heartbeat under slow nodes.

**Implementation:**
1. Introduce versioned `ResearchStarted`, `ResearchProgress`, `ResearchCompleted`, `ResearchFailed`, `ResearchInterrupted` Pydantic or TypedDict contracts. Restrict stages to `planning/discovery/ingestion/retrieval/evaluation/synthesis`; allowlisted bounded counts only.
2. Implement SSE encoding (`event`, single-line JSON `data`, double newline), newline-safe JSON serialization and `: ping` comments; no state, URL, passage or provider text leakage.
3. Map node keys (`plan`, `discover`, `ingest`, `retrieve`, `evaluate`, `synthesize`, `fail`) to safe stage names. Emitting progress after node completion is acceptable; do not invent per-page progress.
4. Integrate domain create/finalize: create `running` → emit `started` → graph stream → validate final state and report → commit domain result → emit terminal. Failed/interrupted commit before terminal event when possible.
5. Catch provider/domain errors and map to safe codes. An error after stream headers must become terminal SSE error if transport still exists; before headers existing JSON envelope may apply.
6. On disconnect or task cancellation, propagate cancellation into graph, close resources and attempt bounded, best-effort `interrupted` persistence. Do not use unbounded shield that keeps expensive work running. A process crash can leave `running` row.
7. Add per-process concurrent stream limit for trusted demo (e.g. configurable semaphore); release permits on all exit paths. Do not add queue.
8. Preserve existing `/run` and its response/error behavior; ideally have both endpoints use the same domain lifecycle, but if retrofitting `/run` requires breaking tests, document and implement a compatible wrapper.

**Tests / acceptance:**
- Fake graph produces `started → progress* → completed`, exactly one terminal event, with report retrievable immediately after completion.
- Failure before start, during graph, after graph but before domain commit, and during SSE write; no false success.
- Simulated disconnect cancels graph task and closes resources; best-effort status write, stale-running fallback.
- Deadline causes interrupted event/status where transport permits; no detached execution.
- Events never expose internal state, raw evidence, prompt or provider error.
- Streaming framing works with `\r\n`, multiline JSON escapes, heartbeats, slow nodes, unknown node keys.
- Same `run_id` in domain, checkpoint config and SSE; no double graph execution.
- Existing Phase 4 tests and `/run` contract remain green.

**Group A review gate:** inspect lifecycle state machine, atomic commit ordering, cancellation and resource cleanup; run backend lint/typecheck/tests, Alembic + streaming integration tests. No frontend yet.

## Group B — Frontend Foundation

### Step 3 — Next.js app, typed API and product shell

**Goal:** reproducible frontend scaffold and stable backend integration.

**Proposed files:**
- New `apps/web/package.json`, lockfile, `next.config.ts`, `tsconfig.json`, `eslint.config.*`, `postcss.config.*`, `.env.example`.
- New `apps/web/src/app/layout.tsx`, `page.tsx`, `globals.css` (or `app/` if selected by generator).
- New `apps/web/src/lib/api.ts`, `types.ts`, `errors.ts`, `sse.ts`.
- New `apps/web/src/components/ui/*` only as needed; optional shadcn/ui.
- Root README/dev scripts changes, CI frontend job.

**Implementation:**
1. Initialize Next.js App Router, TypeScript strict, Tailwind. Commit a lockfile and pinned compatible Node runtime; no unnecessary packages.
2. Configure same-origin `/api/v1/*` proxy/rewrite to FastAPI, validate multipart upload and unbuffered streaming. Avoid leaking provider secrets to client bundles.
3. Implement typed HTTP client for `GET /research/runs`, `GET /runs/{id}`, PDF upload and `POST /research/stream`; define shared TS contracts manually or generate from OpenAPI with a documented repeatable step. Prevent silent backend/frontend contract drift.
4. Implement `fetch` POST SSE parser using streaming `TextDecoder`, handling arbitrary UTF-8 chunk splits, `\r\n`, `\n`, comments, multi-line `data`, malformed frames, unknown events and EOF without terminal. Include `AbortSignal`.
5. Create accessible responsive app shell with title, query composer, recent runs panel, loading and error states; minimal design, no speculative dashboard features.

**Tests / acceptance:**
- TypeScript strict typecheck, lint, build.
- Parser tests: byte fragmentation, Unicode split, CRLF, multiple frames per chunk, heartbeat, malformed JSON, abort, no terminal.
- API contract tests against mocked FastAPI responses; proxy smoke test confirms first SSE event arrives **before** run completion.
- No `NEXT_PUBLIC_OPENAI_API_KEY`/`NEXT_PUBLIC_TAVILY_API_KEY` or secrets in browser.

### Step 4 — Question submission, PDF upload and progress UX

**Goal:** full launch flow from browser to API with honest execution state.

**Proposed files:**
- `apps/web/src/components/research/research-form.tsx`
- `apps/web/src/components/research/pdf-upload.tsx`
- `apps/web/src/components/research/research-progress.tsx`
- `apps/web/src/hooks/use-research-stream.ts` (or equivalent)
- `apps/web/src/app/page.tsx`
- Frontend tests for form, upload and stream state transitions.

**Implementation:**
1. Generate `research_id` in browser when new research draft is created, and keep it stable across upload and run. Validate question length 10–2000, trimmed; backend remains authority.
2. PDF picker supports text PDF guidance, displays selected file and size; submit multipart `research_id` + `file` to existing endpoint; show indexing status separately. Reject obvious oversize client-side while honoring backend response.
3. Start `POST /research/stream` only when uploads are complete (or when user explicitly starts without PDFs). Disable duplicate start. Display run ID after started event.
4. Render stage timeline based on actual events. Long stages show elapsed time/heartbeat status rather than invented percent or per-page counts.
5. Distinguish `completed`, `partial`, `insufficient_evidence`, `failed`, `interrupted`, `disconnected`, `stalled` and `upload_failed` UI states. On completed, fetch authoritative report detail and navigate to `/research/[runId]`.
6. AbortController for Stop action; wording: “Stop this request; research may be interrupted.” No resume/reconnect button. Retry creates new run and clearly indicates this.
7. Clean up abort and listeners on unmount. Do not automatically start a duplicate research when React remounts.

**Tests / acceptance:**
- PDF-first flow uses identical research ID for multipart upload and streaming run.
- Backend validation failures displayed without leaking raw stack traces.
- Duplicate submits suppressed; Stop aborts; disconnect does not show Completed.
- History/report navigation works on completion.
- Keyboard focus, labels, loading states and mobile layout verified.

**Group B review gate:** run frontend lint/typecheck/build/tests, mocked API contract tests and real local proxy streaming smoke test; review cancellation and UX truthfulness.

## Group C — Research Experience & Release Readiness

### Step 5 — Reports, citations and history

**Goal:** make completed research useful after refresh.

**Proposed files:**
- `apps/web/src/app/research/[runId]/page.tsx`
- `apps/web/src/components/research/research-report.tsx`
- `apps/web/src/components/research/citation-link.tsx`
- `apps/web/src/components/research/research-history.tsx`
- `apps/web/src/lib/report.ts`
- Tests for report/citations/history.

**Implementation:**
1. Fetch domain `GET /research/runs/{runId}` on direct navigation and refresh; render run metadata, status, warnings, usage, report title/summary/sections/limitations.
2. Build `evidence_id → EvidenceRef` map from server-attached `report.sources`. Each section's citation IDs resolve through this map, not guessed from model text.
3. For HTTP(S) source URL, render accessible external link with `target="_blank" rel="noopener noreferrer"` and safe URL protocol validation. For PDF source with `page_number`, show “PDF · page N” label without fabricated viewer/download link. Display unknown provenance gracefully.
4. Render report text as escaped React text; do not use `dangerouslySetInnerHTML` on LLM content. If Markdown is added, use a sanitizer and explicitly test XSS vectors.
5. Home history uses paginated domain GET, stable keys, status chips, dates, short question text, next-page button. Do not read LangGraph checkpoints directly.
6. For stale `running` records, show “Last known state: running; execution not confirmed” with timestamp, not “Currently researching”. No automatic resume.
7. Surface `partial`/`insufficient_evidence` prominently, preserving limitations and source transparency.

**Tests / acceptance:**
- Refresh loads saved report and correct citations.
- Citation ID not in sources is rejected by backend model; UI handles invalid legacy data gracefully.
- `javascript:`/`data:` URLs never become links; external links have safe rel.
- PDF page references visible but not clickable without file storage.
- Pagination deterministic; missing run shows 404 UI; interrupted run has no report.
- Long titles/sections and mobile layouts render safely.

### Step 6 — E2E, failure injection, docs and final review

**Goal:** reproducible demo and regression coverage without provider charges in CI.

**Proposed files:**
- Backend integration tests for SSE + PostgreSQL + checkpoint behavior.
- Frontend component/flow tests, optionally Playwright browser E2E if setup cost is reasonable.
- `apps/web/.env.example`, root `.env.example` adjustments, `README.md`, `docs/learning/phase-5.md`, `docs/roadmap.md` status update, CI workflow.

**Test matrix:**

| Scenario | Expected result |
|---|---|
| Question-only success | Progress + durable report + navigable citations |
| PDF-first success | Same `research_id`; PDF indexed before graph |
| Provider failure | Safe terminal failure; history no report |
| Insufficient evidence | Explicit status + limitations |
| User stop | Abort and best-effort interrupted; no false success |
| Network disconnect | Honest disconnected UI; stale-running possible |
| Process crash | No auto-resume; stale-running visible after restart |
| DB failure on report commit | No `completed` event, no completed-without-report |
| SSE proxy buffering | Test catches delayed first event |
| Long ingestion node | Heartbeats, no fake per-page updates |
| Malicious URL or PDF | Existing Phase 3 protections enforced |
| Malicious report text | Escaped; no script execution |
| PDF citation | Page label; no broken viewer link |
| History pagination | Stable cursor, no duplicates |
| Multiple tabs/runs | IDs isolated; per-process concurrency bounded |
| Existing `/run` | Unchanged response and error envelope |

**Quality commands (adapt to actual repo scripts):**

```bash
# backend
cd apps/api
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
# with Compose PostgreSQL/Qdrant available:
uv run alembic upgrade head
uv run python -m nexus.research.checkpoint  # only if setup needed

# frontend
cd ../web
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
```

Check actual scripts and CI before copying commands verbatim. Do not add an undocumented `npm run test` unless a test runner/script is configured. Test frontend streaming against the real dev/proxy stack at least once; mocked fetch alone does not prove streaming.

**Documentation:** local startup order, required env vars, Next.js-to-FastAPI routing, checkpoint setup, no auth/public deployment warning, 10 MiB/100-page PDF defaults, no stored PDF bytes, SSE event schema, cancellation behavior, limitations, known issues, screenshot/demo instructions.

**Group C review gate:** run full backend/frontend quality gate, review schema migrations, verify manual E2E and no API regressions; only then mark Phase 5 complete.

## API/event design sketch (non-binding pseudocode)

```python
# One request owns one run; use one UUID throughout.
run_id = uuid4()
research_id = body.research_id or uuid4()
await history.create_running(run_id, research_id, body.question)
yield sse_started(run_id, research_id)
try:
    async for node_update in graph.astream(
        initial_state(research_id=research_id, run_id=run_id, ...),
        config=run_config(str(run_id)),
        context=context,
        stream_mode="updates",
    ):
        event = safe_progress_from_update(node_update)
        if event is not None:
            yield encode_sse(event)
    final = await get_and_validate_final_graph_state(run_id)
    result = response_from_state(final)
    await history.finalize_success(run_id, result)  # transaction commits here
    yield sse_completed(run_id, research_id, result.status)
except asyncio.CancelledError:
    await best_effort_bounded_interrupt(run_id)
    raise
except Exception as exc:
    await best_effort_finalize_failure(run_id, safe_code(exc))
    yield sse_failed(run_id, safe_code(exc))  # only if transport still open
```

**Warning:** This is conceptual pseudocode, not copy-paste code. A heartbeat needs a concurrent but **bounded and cancellable** streaming wrapper; avoid consuming the graph twice, avoid an unbounded task left running after disconnect, and do not `yield` from a closed transport. Validate final state using LangGraph's supported API and the existing `ResearchState` schema.

## Suggested Cursor workflow

### Prompt — Group A (Plan mode)

> Implement Nexus Research Phase 5, Group A only. Read `docs/specs/phase-5-product-ui/spec.md`, `plan.md`, `docs/adr/ADR-005.md`, current Phase 4 runner/checkpointer/state/graph, SQLAlchemy/Alembic patterns and tests. First propose exact files, migration schema, run state transitions, one-run-ID flow, SSE public event contract, final-state retrieval after `astream`, and cancellation/disconnect tests. Preserve existing `/run` behavior, no workers, no automatic resume, no checkpoint history queries. Identify how to guarantee domain report commit precedes `research.completed`. Do not edit code until I approve the plan.

### Prompt — Group B (Plan mode)

> Implement Nexus Research Phase 5, Group B only. Read Phase 5 SDD and completed Group A contracts. Propose Next.js App Router/TypeScript/Tailwind scaffold, same-origin streaming proxy strategy, typed API client, POST SSE parser tests, question/PDF-first flow and honest progress/disconnect UX. Keep research_id stable across upload and run, and keep provider secrets server-only. First output exact files and tests; do not edit until approved.

### Prompt — Group C (Plan mode)

> Implement Nexus Research Phase 5, Group C only. Read Phase 5 SDD and completed Groups A/B. Propose report/citation/history UI, secure external links, PDF page labels without a viewer, stale-running UX, integration/E2E tests, CI and README updates. Confirm no checkpoint-table queries or public multi-user claims. First output exact files, risks and test matrix; do not edit until approved.

## Definition of Done checklist

- [ ] Domain migration and CRUD endpoints tested against PostgreSQL.
- [ ] Existing synchronous `/run` remains compatible.
- [ ] New request-scoped SSE sends real node progress and exactly one terminal event when connected.
- [ ] Terminal report is committed before completed event.
- [ ] Cancellation and stale-running limitations are explicit and tested.
- [ ] Next.js UI submits question, uploads text PDF, and uses same `research_id`.
- [ ] Streaming parser works under arbitrary network chunking.
- [ ] Reports and history survive page refresh and backend restart.
- [ ] Citations resolve to server provenance; PDF pages are labels only.
- [ ] No auth/worker/storage scope creep; not publicly deployed unprotected.
- [ ] Backend/frontend tests and manual streaming demo pass.
