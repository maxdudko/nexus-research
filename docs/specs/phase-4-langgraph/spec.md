# Nexus Research — Phase 4: LangGraph Specification

**Status:** Proposed for approval  
**Date:** 2026-10-16  
**Branch:** `development`  
**Decisions:** PostgreSQL checkpointing; request-scoped async execution; no background worker.

## 1. Context and objective

Phase 2 supplies `Planner.create_plan(question)` and `Discovery.discover(question)`; Phase 3 supplies `IngestionService.ingest_web(research_id, url)` and `RetrievalService.search(research_id, query, top_k)`. Phase 4 composes these services into a bounded LangGraph workflow that produces a structured, evidence-backed research report. Preserve `/api/v1/research/plan`, `/discover` and Phase 3 evidence endpoints. Phase 5 owns SSE, UI, history UX and optional PDF upload orchestration.

Existing baseline: Python 3.14, `uv`, FastAPI, Pydantic v2, async SQLAlchemy/Alembic/PostgreSQL, Qdrant, OpenAI via LangChain, Tavily. `main.py` owns lifespan DB engine/session factory; `research.py` owns existing plan/discover routes. `Discovery` already has internal caps of 3 Tavily searches and 4 LLM calls per invocation. `ResearchPlan` allows 1–3 tasks and up to 9 search queries. Source evidence is research-scoped but `research_id` is not authentication.

## 2. Functional requirements

### FR-01 — State and graph
- Define a typed `ResearchState` with `research_id`, `run_id`/`thread_id`, question, plan, current search queries, discovered candidates, accepted source IDs, compact retrieved evidence references, evaluation result, iteration count, cumulative budgets, warnings, terminal status and structured report.
- State must be JSON/checkpoint-serializable. No SQLAlchemy sessions, service instances, HTTP clients, raw PDFs, full HTML bodies or embedding vectors in checkpoints. Define serialization for UUIDs and Pydantic models explicitly.
- Build an async `StateGraph` with planning → discovery → ingestion → retrieval → evaluation → conditional refinement or synthesis → END. Define failure/partial terminal paths. Nodes are thin adapters over existing services; no duplicate web fetch, embedding or search logic.
- Avoid unnecessary parallel writes; if adding fan-out later, specify reducers for all shared state keys.
- Explicitly define what a node returns and which fields it updates; guard missing/invalid state.

### FR-02 — Planning and discovery integration
- Reuse `Planner` and `Discovery`. Do not assume `Discovery.discover()` accepts preplanned query lists: current signature accepts a question only. Either compose a bounded query-specific question using approved plan tasks, or make a minimal backward-compatible extension to `Discovery` with tests; no new unrestricted agent loop.
- Deduplicate normalized candidate URLs across iterations, prioritize sources by task relevance and keep deterministic ordering/tie-breaking. Tavily snippets are candidate discovery, **not sufficient evidence for final factual claims**.
- Enforce *global* search and model-call budgets, including calls hidden inside `Discovery` and retries. Record actual usage from `DiscoveryResult.search_calls` and `.llm_calls`; reserve worst-case budget before invoking discovery or expose explicit remaining limits via a backward-compatible adapter. Never merely count one discovery invocation as one search.
- Allow incomplete discovery to proceed with warnings if usable sources exist; no unsupported claims if none exist.

### FR-03 — Ingestion and retrieval
- Index at most the configured number of public web URLs, via `IngestionService.ingest_web`. Do not bypass existing SSRF protection, byte limits, claim-token lifecycle or accepted-version protocol. Handle individual page failures without necessarily failing the entire run; record sanitized warnings and counts.
- Reuse accepted-version `RetrievalService.search`; perform one or more bounded task-relevant queries. Deduplicate by stable `chunk_id` and keep provenance (`source_id`, `research_id`, `url`, `page_number`, text, score). Enforce research isolation end to end.
- Optionally use **previously indexed PDFs** in the same `research_id` scope; Phase 4 `/run` will not accept raw PDF bytes. The API must allow a caller to supply an existing `research_id` for pre-indexed PDF evidence. If new research scope is created, no prior PDF evidence exists.
- Do not persist source text in graph checkpoints beyond short selected excerpts necessary for synthesis; respect checkpoint size bounds. Stable IDs should remain available to reload evidence when appropriate.

### FR-04 — Evidence evaluation and refinement
- Evaluate coverage of plan tasks using a validated structured LLM output: supported/missing task IDs, short gap descriptions, sufficiency recommendation and uncertainty. The model may recommend further research but cannot override deterministic budgets.
- Deterministic router checks iteration count, total searches, pages, LLM calls, remaining deadline and new candidate queries. At most one refinement loop by default. No cycles with identical query sets or no newly indexed evidence.
- Handle zero evidence, partial provider failure and exhausted budgets by returning a **partial or insufficient-evidence report** rather than fabricated factual statements.

### FR-05 — Grounded synthesis and citations
- Generate a Pydantic-validated report: `title`, `summary`, `sections[]` (`heading`, `content`, `citations[]`), `limitations[]`, `status` (`completed|partial|insufficient_evidence`), and an evidence bibliography mapping opaque IDs (`E1`, `E2`, ...) to real source metadata.
- Assign evidence IDs deterministically from the selected evidence snapshot, not from LLM output. Prompt synthesis with a bounded list of untrusted excerpts and IDs. Reject unknown IDs, malformed report shape and citations that refer to evidence outside this run. Never invent URL, PDF page, source ID or timestamps; obtain provenance from retrieval metadata.
- Citation existence is **not** factual entailment. Explicitly document that Phase 6 evaluates whether cited passages support individual claims. Require citations for substantive evidence-based sections; when evidence is insufficient, communicate uncertainty instead of forcing citations.
- Treat retrieved text as untrusted data, never instructions. Cap excerpt size and total synthesis input; avoid full HTML or secrets in prompts/logs.

### FR-06 — PostgreSQL checkpointing
- Use a LangGraph PostgreSQL async checkpointer (compatible `langgraph-checkpoint-postgres` and `AsyncPostgresSaver` API verified against locked versions), backed by the existing PostgreSQL service. Checkpointer schema setup/migrations must be explicit, controlled and idempotent; do not run privileged `setup()` blindly on every HTTP request.
- `thread_id` identifies a **run** and must not be implicitly reused across independent runs. Keep `run_id` distinct from `research_id` to permit future multiple runs per evidence scope. Derive/check thread ID server-side; do not accept arbitrary cross-run thread IDs from unauthenticated clients.
- Persist checkpoints at node boundaries. Checkpointing is **not** a durable job queue: no automatic resumption after process crash. Provide a deliberate recovery strategy for interrupted request-scoped runs; safe to return a recoverable/failed status, with explicit operator or future API restart mechanism. Never claim exactly-once external side effects.
- No raw PDF bytes stored; re-upload required to rebuild lost PDF evidence. Any repeated ingestion node must remain idempotent; web refetches can change content, so checkpoint/resume policy must distinguish already completed node output from re-execution.
- Keep `InMemorySaver` only for tests. Checkpoint database must not leak secrets, large source bodies or other runs' data.

### FR-07 — Execution, API and lifecycle
- Add `POST /api/v1/research/run` accepting a validated question and optional `research_id` UUID; generate a new `research_id` if omitted and always generate a new `run_id` server-side. Return `research_id`, `run_id`, `status`, structured `report`, sanitized `warnings` and compact usage metrics.
- Execution is **async inside one HTTP request** (not synchronous blocking Python). Set a bounded overall deadline (suggest 180s), max iterations (2), max indexed web pages (5), max selected evidence passages (12), and explicit LLM/search budgets. Config defaults are initial proposals; tune after tests and provider timeout review.
- Define behavior when HTTP connection is cancelled: checkpoint may contain last completed node, but execution does not continue in background. Do not return success for an interrupted run. If using `asyncio.timeout`, account for cleanup time separately and preserve cancellation semantics.
- Integrate checkpointer and vector-store clients with FastAPI lifespan or request-scoped async context managers. Do not close shared resources while a run is executing; do not create a new engine per node.
- Preserve current error envelope and provider-not-configured behavior; health endpoints and existing Phase 1–3 APIs must remain unchanged. Avoid leaking raw provider errors, source bodies or secret URLs.

### FR-08 — Persistence and history boundary
- Checkpoint tables are execution-state storage, **not** a public research history API. For Phase 4, the report is returned by `/run` and saved in the final checkpoint. If Phase 5 requires efficient history listing/search, design a separate minimal `research_runs` projection then, rather than querying internal checkpointer tables as a domain API.
- Define explicit retention/cleanup guidance for abandoned checkpoints and associated Qdrant research scopes. No automatic destructive cleanup in this phase.

### FR-09 — Testability and evaluation
- Offline tests use fake Planner, Discovery, IngestionService, RetrievalService, evaluator and synthesizer; no OpenAI/Tavily charges in CI.
- Unit: state serialization, budgets, conditional edges, no-progress stop, dedup, unknown citation rejection, empty evidence, injection-like retrieved content, malformed LLM outputs.
- Integration: compiled graph with fake providers; PostgreSQL checkpointer persistence across graph invocations; interruption at each boundary; repeated node idempotency; end-to-end API; research-scope isolation; lifecycle cleanup.
- Optional live smoke: OpenAI + Tavily + web ingestion + Qdrant + PostgreSQL, explicitly opt-in and budgeted.
- Preserve Ruff, strict mypy, pytest and existing tests.

## 3. Nonfunctional requirements

- End-to-end deadline and deterministic finite loops. No unbounded recursion or autonomous unrestricted tool calls.
- Prefer sequential nodes for MVP. Use async I/O; avoid holding SQL row locks across provider calls.
- Limit checkpoint size, evidence excerpt length, per-run provider calls, pages and search queries.
- Distinguish run `completed`, `partial`, `insufficient_evidence`, `failed` and `interrupted` outcomes; a transport error should not masquerade as an evidence-backed report.
- `research_id` is a functional scope filter, **not** authentication/authorization. The endpoint is not safe for public multi-tenant use without access control and rate limiting.

## 4. API sketch

`POST /api/v1/research/run`

```json
{"question":"What are the practical limitations of autonomous coding agents?","research_id":null}
```

Response (illustrative, not a factual research result):

```json
{
  "research_id":"d40b54d2-ec21-4df4-baa2-58825d19a4cd",
  "run_id":"963e4800-7f8b-47ca-b916-599109d59c16",
  "status":"partial",
  "report":{
    "title":"Limitations of autonomous coding agents",
    "summary":"Illustrative summary of retrieved evidence.",
    "sections":[{"heading":"Context","content":"Illustrative statement.","citations":["E1"]}],
    "limitations":["Some planned topics were not covered."],
    "status":"partial",
    "sources":[{"evidence_id":"E1","source_id":"<uuid>","chunk_id":"<uuid>","url":"https://example.org/article","page_number":null}]
  },
  "warnings":["coverage_incomplete"],
  "usage":{"iterations":2,"search_calls":3,"llm_calls":6,"pages_indexed":2}
}
```

The report shape and usage schema should be frozen in Pydantic before implementation; error cases follow the existing sanitized API envelope. Existing PDF upload is performed through `/api/v1/evidence/pdf` before `/research/run` using the same `research_id`.

## 5. Explicit exclusions

SSE, Next.js UI, background worker, task queue, automatic crash recovery, multi-user authorization, PDF attachment handling in `/run`, scheduled research, multi-agent swarm, OCR, citation entailment classifier, billing, production-scale distributed execution.

## 6. Definition of Done

- [ ] A question runs through compiled LangGraph to a structured report.
- [ ] Existing Planner, Discovery, IngestionService and RetrievalService are reused.
- [ ] Global budgets account for nested Discovery usage and terminate loops.
- [ ] Missing/failed sources produce warnings and partial/insufficient evidence, not fabricated certainty.
- [ ] Report citation IDs resolve to retrieved evidence in the same research scope.
- [ ] Async PostgreSQL checkpoints persist across graph node boundaries and reconnects.
- [ ] Cancellation/deadline does not leave falsely successful runs or unbounded tasks.
- [ ] No unbounded source bodies or embedding vectors are checkpointed.
- [ ] API and existing Phase 1–3 contracts remain compatible.
- [ ] Offline tests, PostgreSQL integration tests and quality gate pass.

## 7. Questions to resolve in Step 0

1. Whether `Discovery` should accept explicit search queries or remain a question-driven tool loop; preserve existing endpoint behavior.
2. Exact cumulative provider budget enforcement given internal SDK retries and Discovery's own caps.
3. LangGraph/checkpointer version and PostgreSQL connection/pool lifecycle compatible with Python 3.14.
4. Structured report fields and evidence ID ordering.
5. Policy for request cancellation and explicit future resumption.
6. Whether Phase 4 needs a minimal `research_runs` table; default **no** until Phase 5 history requirements are finalized.

## 8. References

- `docs/roadmap.md`, `docs/adr/ADR-002.md`, `docs/adr/ADR-003.md`
- `apps/api/src/nexus/ai/planner.py`, `research/discovery.py`, `ingestion/service.py`, `retrieval/service.py`
- https://docs.langchain.com/oss/python/langgraph/overview
- https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres
