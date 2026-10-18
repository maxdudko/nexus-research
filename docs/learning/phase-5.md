# Phase 5 learning notes

The product UI lives in `apps/web`. It asks a question, can index a text PDF first, and follows one run through server-sent events. History and reports are rows in PostgreSQL, not LangGraph checkpoint tables.

## Questions

### Why does the browser call Next.js instead of FastAPI?

The page is `http://127.0.0.1:3000`. The browser posts to same-origin `/api/v1/...`. A route handler in `apps/web/src/app/api/v1/[...path]/route.ts` forwards the method, query, content type, and body to `API_PROXY_TARGET` (default `http://127.0.0.1:8000`) and returns the upstream body without reading it as text. That is what lets the first SSE frame arrive before the run finishes. A `next.config` rewrite buffers POST bodies and streams, so it is not used.

Provider keys stay in the API environment. Nothing in the web app reads `OPENAI` or `TAVILY` variables, and none of them are `NEXT_PUBLIC_*`.

### What does a stream contain?

`POST /api/v1/research/stream` takes `{ "question", "research_id" }`. `research_id` may be null; the server mints one. The server always mints `run_id` and uses that same UUID as the LangGraph thread id and the history row.

Frames are `research.started`, `research.progress`, and at most one terminal event: `research.completed`, `research.failed`, or `research.interrupted`. Comment lines (`: ping`) are heartbeats. `research.completed` carries ids and `completed`, `partial`, or `insufficient_evidence`. It does not carry the report body. That frame is sent only after the report row commits. A failed or interrupted frame is sent only after that status commits. If both writes fail, the stream ends without a terminal frame and the row stays `running`. Nothing resumes it. The UI loads a finished report with `GET /api/v1/research/runs/{run_id}`.

`POST /api/v1/research/run` is unchanged. It still waits for the graph and writes no domain history.

### What is a citation?

Each section lists evidence ids. The UI resolves them through `report.sources`, which the server attached. It does not scan the prose for ids. An `http` or `https` URL becomes a new-tab link with `rel="noopener noreferrer"`. Any other scheme, including `javascript:` and `data:`, stays text. A PDF source shows `PDF · page N` and is not a link. The original PDF bytes are not stored, so there is no viewer to open after refresh.

A citation means that passage was retrieved for this run. It does not mean the sentence is entailed by the excerpt. The page label says the file is not stored, so the page cannot be opened.

### What does Stop do?

Stop aborts the browser request. The proxy forwards that abort, and the API does a best-effort bounded write of `interrupted`. There is no reconnect. A retry creates a new `research_id` and a new `run_id`, says so, and uploads the selected PDF again for that new scope. If the connection drops or the stream errors after `research.started`, the UI reads that run once. A stored `completed`, `partial`, or `insufficient_evidence` opens the report. A stored `failed` or `interrupted` shows that state. A stored `running` row, or a failed read, stays disconnected and says the last known state is running and execution is not confirmed. Stop does not do that read. If the status write itself times out, or the process dies, the row can stay `running`. Nothing resumes it.

`research_id` scopes evidence. It is not an access token. The list and detail routes have no authentication. This app is a local demo, not a public multi-user service.

## Where the failure cases are tested

| Case | Test |
|---|---|
| Question finishes and the report is readable | `apps/api/tests/integration/test_research_stream_integration.py` |
| PDF upload and stream share one `research_id` | `apps/web/src/hooks/use-research-stream.test.tsx` |
| Provider failure is a safe code and has no report | `apps/api/tests/unit/test_research_streaming.py` |
| User abort and a timed-out status write | `apps/api/tests/unit/test_research_streaming.py` |
| Report commit failure does not emit `research.completed` | `apps/api/tests/unit/test_research_streaming.py` and `apps/api/tests/integration/test_research_stream_integration.py` |
| Heartbeat during a slow node | `apps/api/tests/unit/test_research_streaming.py` |
| First SSE byte before the upstream finishes | `apps/web/src/lib/proxy.test.ts` |
| History pages do not overlap and do not read checkpoints | `apps/api/tests/integration/test_research_history_db.py` |
| A second stream is rejected while one holds the permit | `apps/api/tests/unit/test_research_streaming.py` |
| `/run` response and error envelope | `apps/api/tests/unit/test_research_run.py` |
| Unsafe citation URLs, PDF page labels, escaped report text | `apps/web/src/components/research/research-report.test.tsx` |
| Older history page | `apps/web/src/components/research/research-history.test.tsx` |
| Stale `running` copy and a missing run | `apps/web/src/app/research/[runId]/page.test.tsx` |
| A missing terminal frame can leave `running` | `apps/api/tests/integration/test_research_stream_integration.py` |
| PDF retry uploads the new research scope | `apps/web/src/hooks/use-research-stream.test.tsx` |
| One read after an unexpected disconnect | `apps/web/src/hooks/use-research-stream.test.tsx` |
| History reloads after a terminal outcome | `apps/web/src/components/research/research-history.test.tsx` |
| SSE buffer cap and a split CRLF | `apps/web/src/lib/sse.test.ts` |

Phase 3 still owns URL and PDF rejection. Those tests were not rewritten here.

## Demo

1. `docker compose up -d` from the repository root.
2. From `apps/api`, copy `.env.example` to `.env`, run `uv run python -m nexus.research.checkpoint` once, then `uv run alembic upgrade head` and `uv run uvicorn nexus.main:app --reload`.
3. From `apps/web`, copy `.env.example` to `.env.local` and run `npm run dev`. Open `http://127.0.0.1:3000`.
4. Ask a question of 10 to 2000 characters. Optionally choose a text PDF first. The draft id on the page is the `research_id` sent to both the upload and the stream.
5. Watch stages arrive. Elapsed time and the last heartbeat update during a long stage. There is no percent and no per-page bar.
6. When the run finishes, the app opens `/research/{run_id}`. Follow an `https` citation, or read a PDF page label. Refresh that URL.
7. Stop a second run with “Stop this request; research may be interrupted.” It must not show completed.
8. A direct visit to a row whose status is still `running` shows the last known state and does not claim the work is still executing.

## Limitations

- There is no worker, no resume, and no authentication.
- PDF uploads are limited to 10 MiB and 100 pages by default. Bytes are not stored.
- A citation cannot reopen an uploaded PDF.
- List and detail are safe only on a machine you trust. Do not expose them on the public internet.
- Checkpoint tables remain an internal LangGraph detail. The UI never queries them.
