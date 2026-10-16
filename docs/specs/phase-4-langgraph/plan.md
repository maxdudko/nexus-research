# Nexus Research — Phase 4: Detailed Implementation Plan

**Status:** Plan for review; do not implement before approval  
**Date:** 2026-10-16  
**Workflow:** 3 groups × 2 steps; Plan → Build → Tests → Review  
**Decisions:** PostgreSQL checkpointer; request-scoped async execution.

## Step 0 — Baseline, compatibility and contracts (read-only)

Inspect current `development` (not an older PR commit):

```text
docs/roadmap.md
docs/adr/ADR-002.md
docs/adr/ADR-003.md
docs/specs/phase-3-rag/{spec,plan}.md
apps/api/pyproject.toml
apps/api/uv.lock
apps/api/src/nexus/main.py
apps/api/src/nexus/core/config.py
apps/api/src/nexus/api/v1/{router,research,evidence}.py
apps/api/src/nexus/api/errors.py
apps/api/src/nexus/ai/{planner,client,prompts}.py
apps/api/src/nexus/research/discovery.py
apps/api/src/nexus/schemas/research.py
apps/api/src/nexus/ingestion/service.py
apps/api/src/nexus/retrieval/{service,vector_store}.py
apps/api/src/nexus/models/source.py
apps/api/src/nexus/infrastructure/database.py
apps/api/alembic/env.py
apps/api/tests/
```

Validate baseline with `uv sync --frozen`, Ruff, mypy and pytest. Confirm installed Python 3.14 compatibility and lock versions of `langgraph`, `langgraph-checkpoint-postgres` and any pool dependencies. Check current `AsyncPostgresSaver` API, async pool ownership, checkpoint `setup()` requirements, and LangGraph graph compile/invoke signatures against installed versions. Do not copy older examples without verification.

Resolve these design contracts in a short written note:
1. `run_id` distinct from `research_id`; graph `thread_id=run_id`.
2. `Discovery.discover(question)` is question-driven with internal caps 3 searches/4 LLM calls; decide how to enforce **global** budgets and feed planned queries without breaking Phase 2.
3. `ResearchPlan` can contain 3 tasks/9 queries; define task selection and refinement.
4. Define Pydantic report/citation/usage schemas, status/error semantics, stable E-ID assignment.
5. Define checkpoint-safe state types and checkpoint retention/setup strategy.
6. Define cancellation, timeout and rerun semantics, including idempotent ingestion and no automatic resume.

## Proposed file layout

```text
apps/api/
  pyproject.toml                        # langgraph/checkpoint dependencies
  uv.lock                               # resolved versions
  .env.example                          # phase-4 budgets
  src/nexus/core/config.py              # bounded workflow settings
  src/nexus/main.py                     # checkpointer lifecycle (if app-scoped)
  src/nexus/api/v1/research.py          # add /run, keep /plan and /discover
  src/nexus/schemas/report.py           # structured report + citation refs
  src/nexus/schemas/research_run.py     # /run request/response + usage
  src/nexus/research/
    state.py                            # checkpoint-safe TypedDict
    budgets.py                          # deterministic accounting
    routing.py                          # deterministic conditional transitions
    graph.py                            # StateGraph wiring/compile
    runner.py                           # request execution + deadline
    checkpoint.py                       # PostgreSQL checkpointer lifecycle/setup
    nodes/
      planning.py
      discovery.py
      ingestion.py
      retrieval.py
      evaluation.py
      synthesis.py
  tests/unit/test_graph_state.py
  tests/unit/test_graph_budgets.py
  tests/unit/test_graph_routing.py
  tests/unit/test_graph_nodes.py
  tests/unit/test_report_citations.py
  tests/integration/test_graph_workflow.py
  tests/integration/test_graph_checkpoints.py
  tests/integration/test_research_run_api.py
  tests/live/test_research_workflow.py  # optional, only if convention fits
```

Adapt file locations to current project conventions; keep modules cohesive. Avoid generic agent framework abstractions.

---

## Group A — Graph foundation

### Step 1 — State, budgets and deterministic routing

**Goal:** Define graph state and finite control flow before calling any providers.

Implementation:
1. Define `ResearchState` with checkpoint-serializable primitives/typed structures. Prefer small state: question, IDs, plan, query batches, candidate URLs, source IDs, evidence IDs, iteration, cumulative usage, warnings, coverage, report/status.
2. Define global budgets: initial max iterations=2, max web pages=5, max selected passages=12, deadline=180s, one refinement, explicit search/LLM caps. Derive safe caps from nested Discovery calls; if it cannot accept remaining budgets, reserve worst-case cost or extend its API backward-compatibly.
3. Define deterministic routes `continue_discovery`, `synthesize`, `insufficient_evidence`, `fail`. The evaluator recommends; Python policy decides.
4. Define dedup/normalization for queries and URLs; stop when no new candidates or no progress.
5. Define status semantics: completed, partial, insufficient_evidence, failed/interrupted. Distinguish terminal API errors from a valid partial report.
6. Define schema version for checkpoint state; ensure Pydantic/UUID serialization is tested.

Tests:
- Budget exhaustion before any nested discovery call; cumulative accounting across two iterations; no progress stop; malformed state; serialization round trip; cancellation-safe state updates.
- Assert a plan with nine queries cannot accidentally bypass total search caps.

**Gate:** all routes finite under fake data; no external calls.

**Learning:** `TypedDict`, `Annotated` reducers, conditional edges, deterministic vs model-controlled policies, checkpoint serialization.

Suggested commit: `feat(research): define graph state and budgets`.

### Step 2 — Graph construction and existing service adapters

**Goal:** Compile a working skeleton using injected fake services.

Implementation:
1. Implement node adapters for existing `Planner`, `Discovery`, `IngestionService`, `RetrievalService`. Avoid FastAPI imports inside nodes.
2. Build `StateGraph`, add nodes/edges, conditional routing and `END`; pass dependencies via closures/config/runtime context supported by pinned LangGraph version.
3. Keep nodes sequential for MVP. Ensure one node returns only its own state updates; avoid mutating shared nested lists in place.
4. Make discovery selection compatible with existing `Discovery.discover(question)`; explicitly handle plan tasks and dedup.
5. Implement stub evaluator/synthesizer interfaces with deterministic fake responses so the graph can complete end-to-end offline.
6. Set recursion limit as secondary guard, not the only budget.

Tests:
- Full graph traversal, zero results, one retry, no-progress stop, node failure, typed state output; verify existing services called with correct `research_id`.

**Gate:** fake end-to-end workflow terminates and produces predictable state.

Suggested commit: `feat(research): assemble bounded langgraph`.

**Group A review:** check state growth, budget accounting, no nested unbounded agent loops, no API regression.

---

## Group B — Research intelligence

### Step 3 — Evidence evaluation and refinement

**Goal:** Decide what remains unanswered without allowing the model to control execution limits.

Implementation:
1. Define validated `CoverageEvaluation` Pydantic schema: covered task IDs, missing task IDs, gaps, sufficient flag, short uncertainty notes.
2. Use a structured OpenAI model with bounded prompt containing plan and selected evidence. Map provider failures to sanitized warnings or terminal errors per policy.
3. Keep evaluation output untrusted; reject nonexistent task IDs, excessively long gaps and unsupported types.
4. Implement bounded refinement query generation from missing tasks; dedup against prior queries and candidate URLs.
5. Route deterministically: if evidence sufficient → synthesis; else if budget and new work remain → discovery; otherwise partial/insufficient synthesis.
6. Ensure a failed ingestion does not automatically become a successful evidence claim.

Tests:
- Missing evidence, all tasks covered, contradictory evaluator response, provider timeout, identical refinement queries, exhausted budget, no new sources.

**Gate:** every branch terminates and preserves uncertainty.

**Learning:** structured output, model evaluation vs policy, bounded feedback loops.

Suggested commit: `feat(research): evaluate coverage and refine queries`.

### Step 4 — Report synthesis and citation validation

**Goal:** Produce structured grounded reports from accepted evidence only.

Implementation:
1. Define report Pydantic models: title, summary, sections, limitations, status, bibliography with E-ID/source ID/chunk ID/url/page.
2. Assign stable E1..En IDs to selected evidence; freeze evidence snapshot for one synthesis attempt.
3. Build bounded synthesis prompt with clear separation of instructions and untrusted evidence passages.
4. Generate report through existing structured model pattern. Validate citation IDs against selected evidence; reject invented IDs and missing required citations. Resolve provenance exclusively from retrieved metadata.
5. For no evidence, create deterministic insufficient-evidence response without fabricated content. For partial evidence, mark limitations explicitly.
6. Define bounded retry policy for malformed structured output (e.g. one attempt) with accounting against global LLM budget.

Tests:
- Unknown citation IDs; wrong research scope; fabricated URL; missing page; repeated citations; empty evidence; prompt injection in source text; malformed LLM output; partial report.

**Gate:** every cited E-ID resolves to an actual selected passage; report never claims unsupported source metadata.

**Learning:** grounded generation, provenance, schema validation, citation validity vs factual entailment.

Suggested commit: `feat(research): synthesize cited reports`.

**Group B review:** assess citations and source trust, evidence size caps, partial-report semantics, provider budgets.

---

## Group C — Checkpointing, API and reliability

### Step 5 — PostgreSQL checkpointing, runner and HTTP endpoint

**Goal:** Persist graph state and expose request-scoped execution.

Implementation:
1. Add compatible LangGraph and PostgreSQL checkpointer dependencies; lock with uv. Validate exact `AsyncPostgresSaver`/pool API and Python 3.14 compatibility.
2. Create a single explicit checkpoint schema initialization/migration procedure. Use PostgreSQL service, but keep LangGraph checkpoint tables separate from application domain tables. Decide whether initialization belongs in a documented setup command rather than every app startup.
3. Integrate checkpointer lifecycle with FastAPI lifespan or a carefully owned request context. Ensure pool/client cleanup and no session/connection leaks.
4. Implement `ResearchRunner.run(question, research_id|None)` that generates `run_id`, passes `thread_id=run_id`, applies overall deadline, invokes compiled graph asynchronously and validates terminal report.
5. Add `POST /api/v1/research/run` with Pydantic request/response; preserve existing `/plan` and `/discover` routes and error envelope.
6. Keep cancellation propagation correct. A client disconnect/process crash must not imply background continuation; checkpoint recovery must be explicit, not accidental replay. Handle provider failures and partial reports separately.
7. Do not build history listing from checkpoint internals; Phase 5 may introduce a dedicated `research_runs` projection.

Tests:
- Real Postgres checkpoint across reconnect; distinct run IDs with shared research scope; repeated invocation creates new thread; checkpoint setup idempotence; cancellation and deadline; missing keys; DB outage; API schema/errors; lifecycle cleanup.

**Gate:** checkpoint survives connection restart, run IDs are unique, no background task outlives request by design.

**Learning:** async context managers, `asyncio.timeout`, LangGraph checkpointers, state persistence vs durable execution.

Suggested commit: `feat(research): add postgres checkpoints and run endpoint`.

### Step 6 — Integration, quality gate and demo

**Goal:** Demonstrate question → cited report, with controlled failures and observable usage.

Implementation:
1. Add fake-provider end-to-end graph/API tests; test provider exception at every node and verify terminal status/warnings.
2. Integration-test PostgreSQL checkpointing, Qdrant research scope and ingestion idempotency. Use local Compose services; no paid calls in default CI.
3. Run a manual opt-in live scenario: real question → plan → Tavily → safe HTML → embedding → Qdrant retrieval → coverage → report. Capture sanitized usage counts, status and citation URLs.
4. Run scenario with pre-indexed PDF using the same research_id; verify PDF page citations without uploading PDF via `/run`.
5. Compare estimated vs actual nested Discovery calls and overall elapsed time; tune budget defaults, ensure proxy timeout assumptions documented.
6. Update README, `.env.example`, roadmap, architecture notes and developer learning log. Document no auto-resume, no durable PDF storage, no auth and no SSE.
7. Run full quality gate and review dependency changes, migrations and accidental secret logging.

Commands from `apps/api`:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m 'not integration and not live'
# Start local PostgreSQL/Qdrant from repository root, then:
uv run pytest -m 'integration and not live'
# Live provider tests only when deliberately enabled:
uv run pytest -m live -o addopts=''
```

If integration test commands or marker conventions differ in the current branch, adapt to existing project configuration rather than overwriting it.

Manual DoD:
- [ ] Report produced with real, verifiable URL citations.
- [ ] Pre-indexed PDF passage appears with correct page number.
- [ ] No cross-research evidence leaks.
- [ ] Two iterations maximum and total nested provider calls bounded.
- [ ] No evidence → insufficient-evidence report.
- [ ] One failed page → partial outcome with warning, if other evidence exists.
- [ ] Checkpoint persists across DB reconnect; no implicit background resume.
- [ ] Interrupted request cannot claim completed status.
- [ ] CI and Phase 1–3 regression suite pass.

Suggested commit: `test(research): verify graph workflow and checkpointing`.

**Group C review:** verify full graph state, database lifecycle, resource cleanup, failure injection and end-to-end citations.

---

## Cursor prompts

### Group A — Plan only

> Read Phase 4 spec.md, plan.md, ADR-004.md and current `development` code. Plan Steps 1–2 only: checkpoint-safe typed state, deterministic global budgets that account for nested `Discovery.discover()` limits, graph nodes/adapters, conditional routing and offline fake-provider tests. Identify exact files and API compatibility constraints. Explain Python/LangGraph concepts compared with TypeScript/NestJS. Do not edit until approved.

### Group B — Plan only

> Review completed Group A and approved Phase 4 docs. Plan Steps 3–4 only: structured coverage evaluation, deterministic bounded refinement, synthesis from accepted evidence, server-assigned citation IDs, citation validation, partial/insufficient reports, injection resistance and tests. Do not edit until approved.

### Group C — Plan only

> Review Groups A/B and Phase 4 docs. Plan Steps 5–6 only: PostgreSQL AsyncPostgresSaver integration, explicit setup, request-scoped async runner, `/api/v1/research/run`, cancellation/deadline behavior, checkpoint recovery limits, full offline/integration tests and live smoke. Verify installed package APIs before designing integration. Do not edit until approved.

## Stop conditions

- Stop if nested Discovery budget cannot be enforced without violating global limits.
- Stop if checkpoint serialization contains non-serializable clients, sessions or unbounded source text.
- Stop if the report can cite a nonexistent passage or a different research scope.
- Stop if the HTTP request detaches work silently or implies automatic crash recovery.
- Stop if a failed workflow is reported as completed or existing Phase 1–3 APIs regress.

## Learning checklist

- [ ] Explain `StateGraph`, nodes, edges, conditional edges and reducers.
- [ ] Explain LangGraph checkpoint `thread_id` versus domain `research_id`.
- [ ] Explain why a checkpoint is not a queue or a transaction.
- [ ] Explain how global budgets account for nested tool loops.
- [ ] Explain evidence coverage evaluation vs deterministic routing.
- [ ] Explain citation ID validation vs factual grounding.
- [ ] Explain async request cancellation and idempotent node replay.
- [ ] Explain how Phase 5 can add SSE/history without rewriting nodes.
