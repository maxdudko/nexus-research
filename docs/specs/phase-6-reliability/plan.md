# Phase 6 — Reliability & Evaluation: Implementation Plan

**Status:** Proposed  
**Branch:** `development`  
**Prerequisites:** Phase 5 completed and PR #12 hardening considered  
**Source of truth:** `spec.md`, `docs/adr/ADR-006.md`  
**Workflow:** Plan → Build → Tests → Review; three independent review gates, six steps.

## 0. Before coding: baseline and invariants

1. Confirm current `development` HEAD, tests and file layout in Cursor; this plan is grounded in the branch inspected 2026-10-18, not an immutable SHA. Inspect current PR #12 changes if not yet merged.
2. Run existing API and frontend checks; capture passing baseline and current known failures before Phase 6 changes.
3. Read: `nexus/research/{runner,coordinator,history,graph,budgets}.py`, `nexus/ai/{client,planner,coverage,synthesis}.py`, `nexus/retrieval/{service,embeddings}.py`, `nexus/tools/web_search.py`, `nexus/ingestion/service.py`, `nexus/core/config.py`, `nexus/main.py`, `nexus/schemas/report.py`.
4. Confirm how `ChatOpenAI.with_structured_output()` exposes response usage in the current pinned version; do not assume `AIMessage.usage_metadata` survives Pydantic conversion. Verify embedding usage availability too.
5. Preserve invariants: one run_id across layers; `research_id` scopes evidence; no background resume; completion only after domain commit; no raw ResearchState over SSE; GraphUsage logical counts unchanged; external side effects at-least-once; no original PDF storage.

## Group A — Observability (Steps 1–2)

### Step 1 — Structured logging and optional OTel tracing

**Goal:** safe, correlated instrumentation with no mandatory external service.

**Suggested files (adapt to existing layout):**

```text
apps/api/src/nexus/observability/__init__.py
apps/api/src/nexus/observability/context.py
apps/api/src/nexus/observability/logging.py
apps/api/src/nexus/observability/tracing.py
apps/api/src/nexus/observability/spans.py
apps/api/src/nexus/core/config.py
apps/api/src/nexus/main.py
apps/api/src/nexus/research/{runner,coordinator}.py
apps/api/pyproject.toml
apps/api/tests/unit/test_observability_*.py
```

**Implementation checklist:**

- [ ] Add pinned-compatible `opentelemetry-api`, `opentelemetry-sdk` and **optional** OTLP exporter dependencies only as needed; verify Python 3.14 compatibility before lock update.
- [ ] Add `NEXUS_OTEL_ENABLED=false`, optional OTLP endpoint, service name, and log format settings. No collector must be required for app startup or tests.
- [ ] Configure tracer provider once in FastAPI lifespan; use no-op/disabled tracing when not enabled; clean shutdown with bounded flush. Avoid overriding a host-provided global provider unexpectedly.
- [ ] Define typed allowlisted span/log attributes and helper context manager for operation durations. Include run_id/research_id/stage/iteration and safe error codes.
- [ ] Propagate contextvars across `_drive` task and SSE generator; test span parent-child relationships.
- [ ] Wrap graph stage transitions at existing node/runner boundaries without changing graph topology. Prefer explicit spans to serializing telemetry into ResearchState.
- [ ] Emit JSON logs with UTC timestamps and sanitized fields; do not emit prompt, query, URL query string, raw exception, PDF content, embedding vector, provider response or credentials.
- [ ] Ensure exporter failures and logging exceptions do not change research status; bounded buffers and sampling.

**Tests:** disabled mode no network; in-memory span exporter; parent-child context in `asyncio.create_task`; exception and cancellation closure; redaction capture with synthetic secret/prompt; lifecycle startup/shutdown; exporter failure is non-fatal.

**Acceptance:** trace and logs correlate one run and stages; app runs unchanged without OTLP; no sensitive content in logs/spans.

### Step 2 — Provider usage, timing and estimated cost

**Goal:** separate actual/estimated provider usage from graph budget accounting.

**Suggested files:**

```text
apps/api/src/nexus/observability/{usage,costs,pricing}.py
apps/api/src/nexus/ai/client.py
apps/api/src/nexus/retrieval/embeddings.py
apps/api/src/nexus/tools/web_search.py
apps/api/src/nexus/research/discovery.py
apps/api/src/nexus/research/runner.py
apps/api/tests/unit/test_usage.py
apps/api/tests/unit/test_costs.py
```

**Implementation checklist:**

- [ ] Define `OperationUsage` and `CostEstimate` Pydantic models; `None` for unavailable tokens/attempts/prices, never silently 0.
- [ ] Instrument each controlled Tavily attempt inside `_call_with_retries`; distinguish physical attempts from `DiscoveryResult.search_calls`.
- [ ] Instrument OpenAI structured calls at the narrowest safe wrapper boundary; prototype metadata capture and keep original validated return types intact. If metadata cannot be observed, emit unavailable rather than changing schemas or parsing raw responses unsafely.
- [ ] Instrument embedding batches and query embeddings separately; capture duration/batch size and provider usage if available. Estimated token counts must be tagged `estimated` and not mixed with provider-reported counts.
- [ ] Track `duration_ms`, provider, operation, model, outcome and known tokens; do not log input text or returned vectors.
- [ ] Introduce versioned pricing table/configuration with effective date and unit; calculate Decimal USD estimates only from known quantities; return `complete`, `partial` or `unavailable` cost coverage.
- [ ] Avoid synchronous SQL writes per provider call. If storing an aggregate, design a separate opt-in proposal; do not change `GraphUsage` or report API contract in this step.
- [ ] Add tests for unknown metadata, retries, duplicate callbacks, cancellation, missing prices, arithmetic, and no sensitive data in telemetry.

**Acceptance:** a mocked run yields useful timing and usage observations, while `GraphUsage` remains byte-for-byte API compatible. Cost never falsely claims complete precision.

### Review Gate A

- [ ] Ruff + mypy strict + pytest pass.
- [ ] No mandatory collector or network calls in tests.
- [ ] Review secret/prompt redaction and span cardinality.
- [ ] Validate the actual LangChain structured-output usage behavior in a focused test.
- [ ] Review OTel overhead and safe exporter failure behavior.

**Do not begin Group B until Gate A is reviewed.**

## Group B — Evaluation (Steps 3–4)

### Step 3 — Offline retrieval and citation evaluation

**Goal:** deterministic, versioned metrics and provenance checks.

**Suggested files:**

```text
apps/api/src/nexus/evaluation/__init__.py
apps/api/src/nexus/evaluation/{schemas,datasets,retrieval,citations,runner}.py
apps/api/src/nexus/evaluation/__main__.py
apps/api/tests/evaluation/fixtures/{corpus.json,queries.json,retrieval_outputs.json,citation_cases.json}
apps/api/tests/evaluation/{test_metrics,test_citations,test_runner}.py
docs/evaluation/methodology.md
```

**Implementation checklist:**

- [ ] Create 15–20 authored fixture queries and short documents with stable semantic IDs, labels and dataset version; no copyrighted/private user content.
- [ ] Define `Recall@K`, `Precision@K`, `MRR@K` precisely, including duplicate hits, missing gold, zero relevant documents, tie ordering and K bounds.
- [ ] Implement pure deterministic scorers with unit tests using hand-calculated examples.
- [ ] Add an adapter that maps real `EvidenceMatch`/`EvidenceRef` IDs to fixture semantic IDs; do not assume production UUIDs are stable across re-indexing.
- [ ] Add citation validation: IDs unique and resolvable, scope identity, source/chunk mapping, PDF page/URL consistency; assert no cross-research references.
- [ ] Test valid IDs with unsupported content to prove validity and factual support are separate metrics.
- [ ] Add CLI `uv run python -m nexus.evaluation --suite offline --output ...` with versioned JSON schema and deterministic ordering.
- [ ] Offline PR suite uses frozen retrieval outputs; optional local Qdrant + fake embeddings tests the retrieval adapter, not real embedding quality. Document distinction.
- [ ] Produce baseline only by actually running the suite; never hardcode invented performance results.

**Tests:** exact metric arithmetic, empty gold behavior, duplicate hits, missing evidence, foreign research_id, malformed citation IDs, CLI exit code and JSON schema.

**Acceptance:** repeat runs produce identical JSON metrics for same fixtures/code; no OpenAI/Tavily keys required.

### Step 4 — Grounding evaluation and optional LLM judge

**Goal:** calibrated claim-level support assessment without modifying user reports.

**Suggested files:**

```text
apps/api/src/nexus/evaluation/{grounding,judge,calibration}.py
apps/api/tests/evaluation/fixtures/grounding_cases.json
apps/api/tests/evaluation/test_grounding.py
apps/api/tests/live/test_grounding_judge_live.py
docs/evaluation/{methodology,baseline}.md
```

**Implementation checklist:**

- [ ] Create at least 10–15 manually labeled claims: supported, unsupported, insufficient_context, contradictory sources, spurious valid citation, uncertain answer.
- [ ] Build deterministic input validator that only sends cited, bounded evidence excerpts to judge; no tool access, no free-form prompt execution, no foreign source references.
- [ ] Define structured judge response with verdict, short rationale, evidence IDs, schema validation and bounded retry/timeout.
- [ ] Keep judge entirely outside LangGraph production routing and synthesis; no report mutations.
- [ ] Require explicit `--suite live` or `pytest -m live`, credentials, budget cap and documented estimated spend. Default `pytest` excludes live via existing marker config.
- [ ] Record judge model/version, prompt version, dataset version, timestamp, usage availability and settings. Compare with human gold using confusion matrix and agreement; label scores as estimates.
- [ ] Use fake judge in offline tests to validate orchestration and schema. No CI dependency on stochastic judge output.

**Tests:** no accidental paid call, invalid judge schema, unknown citation, contradictory evidence, timeout, judge disagreement, non-mutating behavior, redacted judge telemetry.

**Acceptance:** deterministic checks gate PRs; live judge is optional, reproducible enough to compare versions and explicitly not a ground-truth oracle.

### Review Gate B

- [ ] Evaluation fixture corpus is small, versioned and license-safe.
- [ ] CI tests are fully offline and deterministic.
- [ ] Citation validity and claim support reported separately.
- [ ] Review actual computed baseline, not invented thresholds.
- [ ] LLM judge does not touch production graph or require credentials during import.

**Do not begin Group C until Gate B is reviewed.**

## Group C — Failure testing and regression gates (Steps 5–6)

### Step 5 — Reliability and adversarial test matrix

**Goal:** prove bounded failure behavior and existing security controls.

**Suggested files:**

```text
apps/api/tests/reliability/test_provider_failures.py
apps/api/tests/reliability/test_graph_limits.py
apps/api/tests/reliability/test_history_failures.py
apps/api/tests/reliability/test_stream_lifecycle.py
apps/api/tests/reliability/test_prompt_injection.py
apps/api/tests/reliability/test_url_safety.py
apps/api/tests/reliability/test_pdf_limits.py
docs/evaluation/failure-matrix.md
```

**Implementation checklist:**

- [ ] OpenAI: 429, timeout, malformed structured output, provider unavailable, model refusal, SDK retries unknown; assert safe error and bounded attempts.
- [ ] Tavily: retryable timeout, 429, bad results; assert logical budget is not silently increased and physical attempt telemetry is honest.
- [ ] Qdrant: indexing/search unavailable; no invented evidence or cross-scope leakage.
- [ ] PostgreSQL: failure on run insert, success commit, failure commit, checkpoint write, cancellation persistence; assert no `research.completed` before committed report.
- [ ] SSE: disconnect mid-graph, EOF after committed report, heartbeat, terminal event exactly once, stream semaphore capacity; retain best-effort semantics.
- [ ] Graph: 2 iterations/6 searches/13 logical LLM calls/5 pages/12 passages/180s defaults respected (using injected clocks and settings, no 180-second sleeps).
- [ ] URL safety: redirect to private/loopback/link-local/metadata addresses, DNS rebinding, oversized web body; mock DNS/network; never access real unsafe targets.
- [ ] PDF: over 10 MiB, over 100 pages, malformed/scanned PDFs as supported; assert bounded failure.
- [ ] Prompt injection: authored web/PDF excerpts attempting role takeover, secret exfiltration, citation fabrication or forced external links. Test actual report outcome with fakes; acknowledge limitations of prompt-only mitigation.
- [ ] Fix only reproduced defects; add regression tests before implementation changes.

**Acceptance:** failure matrix documents expected API/status/checkpoint outcomes and limitations; tests complete offline and in bounded time.

### Step 6 — CI integration, baseline and handoff

**Goal:** make reliability a repeatable development practice.

**Suggested files:**

```text
.github/workflows/ci.yml
apps/api/pyproject.toml
docs/evaluation/{methodology,baseline,failure-matrix}.md
README.md
docs/roadmap.md
```

**Implementation checklist:**

- [ ] Add explicit offline evaluation CLI command to CI after lint/typecheck and unit tests; fail only on deterministic schema/provenance/regression violations.
- [ ] Keep `pytest -m live` / `--suite live` out of normal PR workflows. Document credentials, approximate expense and manual run steps.
- [ ] Generate and commit measured baseline JSON or document its exact reproducible location. Include dataset version, scorer version, commit, runtime and limitations.
- [ ] Document trace setup with tracing disabled, optional OTLP endpoint, privacy policy, cost coverage caveats and operation counters.
- [ ] Update README developer commands and roadmap Phase 6 status only when criteria are met.
- [ ] Re-run API Ruff/mypy/pytest, offline evaluation, integration tests with Compose when available, and frontend checks only if touched.
- [ ] Perform final diff review for unnecessary dependencies, migrations, changed public API, leaked secrets, data retention, non-deterministic tests and runaway instrumentation.

**Acceptance:** a new contributor can run an offline reliability suite without credentials, understand its limits and optionally inspect OTel traces with an explicitly configured collector.

### Review Gate C / Definition of Done

- [ ] All Phase 6 acceptance criteria in `spec.md` checked.
- [ ] No changes to Phase 4 graph topology or Phase 5 request-scoped lifecycle unless justified by a verified defect.
- [ ] No fabricated cost or quality claims.
- [ ] Security and privacy tests pass.
- [ ] CI fully offline, paid judge manual-only.
- [ ] `baseline.md` contains measured values and provenance.
- [ ] Phase 7 can deploy demo without requiring tracing backend or evaluation credentials.

## Implementation order and suggested commits

| Step | Commit focus | Review gate |
|---|---|---|
| 1 | `feat(observability): add optional tracing and safe JSON logs` | A |
| 2 | `feat(observability): track provider usage and cost coverage` | A |
| 3 | `feat(evaluation): add offline retrieval and citation suite` | B |
| 4 | `feat(evaluation): add optional calibrated grounding judge` | B |
| 5 | `test(reliability): cover failures and injection scenarios` | C |
| 6 | `ci(reliability): gate offline eval and publish baseline` | C |

## Cursor prompts

### Group A — Plan prompt

> Read `docs/specs/phase-6-reliability/spec.md`, `docs/adr/ADR-006.md`, and this plan. Inspect the **current development branch** before coding. Plan Step 1 and Step 2 only: optional OTel instrumentation, redacted JSON logging, independent provider usage/cost observations. Confirm Python 3.14 dependency compatibility and experimentally verify whether the existing LangChain structured-output wrappers expose token metadata. Preserve `GraphUsage`, request-scoped SSE, LangGraph topology and existing provider behavior. List exact file changes, tests and risks; do not implement until approved.

### Group B — Plan prompt

> Review Group A implementation and Phase 6 documents. Plan Step 3 and Step 4 only: versioned synthetic retrieval/citation fixtures, deterministic scoring and CLI JSON output, and an opt-in calibrated claim-level LLM judge. Offline tests must not access network or require API keys. Distinguish citation validity from entailment. Explain mapping between stable fixture IDs and production chunk UUIDs. Provide test plan and proposed baseline process before implementation.

### Group C — Plan prompt

> Review completed Groups A and B and Phase 6 documents. Plan Step 5 and Step 6 only: failure injection, prompt-injection/SSRF/resource-limit regression tests, CI offline evaluation gate and measured baseline docs. Preserve best-effort cancellation, terminal DB commit before SSE completion, and no automatic resume. Fix only reproducible bugs with tests. Provide exact file changes and verification commands before implementation.

## Verification commands (adjust to repo scripts)

```bash
cd apps/api
uv sync --group dev
uv run ruff check .
uv run mypy src
uv run pytest
uv run python -m nexus.evaluation --suite offline --output ../../artifacts/evaluation/offline.json
# Optional, never CI default:
# uv run pytest -m live
# uv run python -m nexus.evaluation --suite live --output ../../artifacts/evaluation/live.json
```

The evaluation CLI commands are **target commands to implement**, not existing commands at baseline. Do not claim they currently work. Use the repository's actual CI paths and existing Compose integration test instructions.
