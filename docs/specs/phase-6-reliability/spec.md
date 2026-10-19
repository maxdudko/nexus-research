# Phase 6 — Reliability & Evaluation: Specification

**Status:** Proposed  
**Project:** Nexus Research  
**Baseline:** `development` inspected 2026-10-18  
**Decision:** `docs/adr/ADR-006.md`  
**Plan:** `docs/specs/phase-6-reliability/plan.md`

## 1. Objective

Make research runs explainable, measurable, safe under expected failures, and regression-testable. Add OpenTelemetry-compatible, opt-in tracing; structured redacted logs; per-operation latency, usage and estimated costs; offline deterministic retrieval/citation evaluation; optional calibrated LLM grounding evaluation; failure-injection and adversarial tests. Preserve request-scoped execution, domain history, bounded LangGraph and existing provider adapters.

**Selected decisions:** `tracing=otel`, `grounding=hybrid`, `CI=offline`. An exporter/collector, LangSmith, paid evaluation, dashboards and live provider credentials are not required to run CI or start the application.

## 2. Current implementation and integration points

- Python `>=3.14,<3.15`; FastAPI, SQLAlchemy async, PostgreSQL, Qdrant; `uv`, Ruff, mypy strict, pytest (`apps/api/pyproject.toml`).
- `nexus.main.create_app()` and `lifespan()` own process resources. Instrument once at app startup; shut down providers/exporters without blocking shutdown indefinitely.
- `nexus.research.runner.ResearchRunner` drives `graph.astream(..., stream_mode=["updates", "values"])`; `GraphProgress` represents completed nodes, not sub-node operations. `run_id` is the LangGraph `thread_id`.
- `nexus.research.coordinator.RunCoordinator` commits domain report/status before terminal SSE, with best-effort cancellation persistence. Do not alter this contract.
- `nexus.research.history` persists `research_runs`/`research_reports`; checkpointer tables are separate. Avoid schema migrations unless a measured need is demonstrated.
- `nexus.research.budgets` counts logical calls, including Discovery's nested ceiling. `GraphUsage` has `iterations`, `search_calls`, `llm_calls`, `pages_indexed`, `pages_failed` and expressly excludes SDK retries.
- `nexus.ai.client.StructuredOutputModel` / `OpenAIStructuredModel` call `ChatOpenAI.with_structured_output(..., method="json_schema")`; token usage visibility must be verified for these wrappers. `nexus.retrieval.embeddings.OpenAIEmbedder` calls `OpenAIEmbeddings.aembed_documents`, including query embedding through the same path. `nexus.tools.web_search.WebSearch` performs retry attempts internally.
- `nexus.retrieval.service.RetrievalService.search()` enforces research-scope and accepted-version filtering. `nexus.ai.synthesis.assemble_report()` validates cited evidence IDs but not semantic entailment. `nexus.ai.coverage` provides model coverage advice.
- Existing settings include 180s graph deadline, 2 iterations, 6 logical search calls, 13 logical LLM calls, 5 page attempts, 12 passages, 2 concurrent streams, 15s SSE heartbeat. Do not silently increase these limits.
- `docs/roadmap.md` Phase 6 explicitly calls for tracing/logging/cost, retrieval/citation/grounding evaluation and provider/timeout/injection/URL/resource-limit tests.

## 3. Scope / non-goals

**In scope:** opt-in OTel spans; JSON logs with safe structured fields; operation counters and timing; optional token/cost estimation; fixture-driven offline metrics; optional manual live judge; failure and injection tests; machine-readable evaluation output; reproducible baseline and CI quality gate.

**Out of scope:** worker queues, automatic resume, exactly-once provider operations, a mandatory collector, Prometheus/Jaeger deployment, auth/multi-tenant, arbitrary production trace storage, model training, a UI redesign, changing LangGraph topology, automatic report rewriting based on judges, full billing accuracy.

## 4. Functional requirements

### FR-01 — Correlation and traces

- Every research execution must correlate `run_id`, `research_id`, operation/stage, iteration and outcome; include trace/span IDs in logs when tracing is enabled.
- Instrument request/run and graph stages (`plan`, `discover`, `ingest`, `retrieve`, `evaluate`, `synthesize`), and provider/tool boundaries (OpenAI chat, embeddings, Tavily, web fetch, Qdrant search) where an explicit wrapper is feasible.
- Prefer a small observability adapter / context manager and explicit attributes over widespread vendor-specific decorators. Span attributes: `nexus.run_id`, `nexus.research_id`, `nexus.stage`, `nexus.iteration`, `nexus.operation`, `nexus.outcome`, `duration_ms`, numeric usage. Never attach question, prompt, completion, PDF text, URLs with query secrets, headers, raw exception messages or API keys by default.
- `NEXUS_OTEL_ENABLED=false` must be a safe default. No collector configured must mean no exporter failure, startup error or implicit network access. If enabled, use documented OTLP endpoint and explicit configuration; exporter errors must not change research status. Document sampling and resource/service name.
- Context propagation must work across `ResearchRunner._drive()` child task and SSE lifecycle. No trace context is serialized into LangGraph checkpoints.
- Tracing must be optional in unit tests and testable with an in-memory exporter or spy; test span closure on exception/cancellation.

### FR-02 — Structured logs

- JSON logs in configured mode; `event`, timestamp UTC, level, safe error code, run ID, stage, duration and trace IDs when available.
- Central redaction policy: never log `SecretStr`, authorization/cookies, prompt text, evidence excerpts, raw PDF/web content, embedding vectors, provider response bodies, or full URLs with credentials/query strings.
- Provider failures use existing safe `ResearchError.code` taxonomy; unexpected failures log exception type and correlation identifiers, not raw content. A developer-only diagnostic mode may be considered separately, but is out of scope by default.
- Logs must not become a second source of truth for run state; `research_runs` remains authoritative for history.

### FR-03 — Metrics and costs

- Keep `GraphUsage` unchanged: it is a **logical graph budget**, not billable requests.
- Introduce an independent `OperationUsage` record with `run_id`, stage, provider, operation, model, elapsed milliseconds, outcome, logical operation count, known physical attempts, known input/output/embedding tokens, and `usage_source` (`provider|estimated|unavailable`). Unknown metrics remain `null`, not zero.
- Retry accounting: instrument actual attempts only where code controls them (e.g. Tavily `_call_with_retries`); SDK-managed OpenAI retries are unknown unless a supported provider-level hook can observe them. Never infer physical attempts from `GraphUsage`.
- Token usage from structured LangChain calls must be validated in tests; if the wrapper does not expose provider usage, mark unavailable rather than fabricating counts. For embeddings, don't assume token counts are returned by the abstraction; estimate separately with explicit provenance or report unavailable.
- Cost is an **estimate**, computed from versioned price configuration and reported tokens / Tavily unit costs only when available. Include currency USD, pricing snapshot/version, breakdown and coverage flag (`complete|partial|unavailable`). A partial estimate must not be presented as total billable spend.
- Prefer in-process event collection and optional bounded run aggregate output; do not add per-token SQL writes or migrations unless justified. Logs/OTel may carry per-operation metrics; report aggregate can be written to an evaluation artifact. Metrics failures must never abort the run.
- Counters/timers must be monotonic and non-negative. Bounded cardinality: no run IDs as metric labels in global counters; keep IDs on spans/logs only.

### FR-04 — Deterministic retrieval benchmark

- Commit a small, versioned fixture corpus and gold query-to-relevant-document/chunk mapping. Target 15–20 cases across single-source, multi-source, paraphrase, irrelevant and PDF/web provenance scenarios.
- Fixture IDs must be stable across runs; choose semantic fixture IDs rather than random Qdrant UUIDs for scoring, with an explicit mapping to indexed chunk IDs. Pin chunking and embedding fixture versions. A benchmark using fake retrieval can test scorer correctness, **not** actual retrieval quality; label results accordingly.
- Compute `Precision@K`, `Recall@K`, `MRR@K` and sample counts, using deterministic tie/order behavior and explicit treatment of no relevant items. `Recall@K` denominator is all gold relevant IDs; `MRR@K` uses rank of first relevant hit.
- Offline PR gate: deterministic scorer tests and frozen retrieval-output fixture regression. Optional integration benchmark against a local deterministic fake embedder + Qdrant; live embedding benchmark is opt-in, not a PR gate.
- Emit versioned JSON with dataset version, metric definitions, case outcomes and aggregate metrics. Record baseline; do not invent performance numbers.

### FR-05 — Citation and grounding evaluation

- Deterministic checks: report schema validity, unique `EvidenceRef.evidence_id`, citations resolve to known snapshot, canonical research-scope identity, source/chunk provenance, URL/page metadata consistency, and no foreign evidence. Distinguish these checks from factual support.
- Create a claim-level fixture dataset with human-labeled `supported`, `unsupported`, `insufficient_context` and evidence excerpts; include false-but-valid citation IDs, misleading quotes, conflicting sources, and abstention/uncertainty.
- Offline deterministic evaluator checks structural correctness and compares known expected outputs; a separate **optional** LLM judge receives claim + bounded cited excerpts and returns a structured verdict, rationale and evidence IDs. Judge cannot modify reports, call tools or control graph routing.
- Judge results are probabilistic; require explicit opt-in (`pytest -m live` or documented CLI flag), API credentials and cost warning. Store model, prompt version, dataset version, temperature/settings if supported, timestamp and cost/usage provenance. Calibrate against a manually reviewed gold subset and publish confusion matrix/agreement; never treat judge score as ground truth.
- Avoid falsely claiming exact factual-grounding rates from citation validity alone.

### FR-06 — Failure injection and security

- Add deterministic tests for OpenAI timeout/429/unavailable/malformed output, Tavily retries/rate-limit, Qdrant failure, PostgreSQL commit/checkpoint failure, graph deadline, cancellation, SSE EOF after commit, missing token usage, exhausted budgets and partial reports.
- Validate existing web URL SSRF controls including redirects, loopback/private/link-local/metadata addresses and DNS rebinding protections **as implemented**, with mocks and no real unsafe requests. Do not weaken existing checks.
- Adversarial fixtures: page/PDF passage instructs assistant to ignore system rules, exfiltrate secrets, cite nonexistent evidence or include unrelated content. Verify safe behavior at graph/report boundary; do not log injected text. Explicitly record limitations of prompt-only defenses.
- Check PDF size/page limits, web payload limits, oversized output/SSE frame, and concurrent stream semaphore; tests must be bounded and offline.
- Failure tests must assert user-visible safe error code and durable state where persistence is available; crashes may leave stale `running` rows. No automatic resume guarantee.

### FR-07 — Developer execution and CI

- Add `python -m nexus.evaluation` or an equivalent documented `uv run` CLI with `--suite offline`, `--output <path>` and a separate explicit `--suite live` mode. Offline must not require keys or external network.
- CI runs Ruff, mypy, pytest offline, evaluation scorer/regression suite and validates generated JSON schema. Paid eval is manual only, with no GitHub PR secrets requirement.
- Separate **hard gates** (schema, provenance, no foreign citations, resource limits, fixture regressions) from **informational metrics** (LLM judge grounding, live Recall@K, cost). Thresholds must be documented and based on a committed baseline; no arbitrary accuracy threshold without measured data.
- Document how to regenerate baseline, compare results, and explain false positives/limitations.

## 5. Public interfaces and data contracts

Do not change existing `/api/v1/research/run`, `/stream`, history, PDF upload or SSE event names for Phase 6. Diagnostic data is internal/offline-first; if adding an optional developer diagnostics endpoint, it must be disabled by default and never leak prompts or source content. Prefer no new public endpoints in this phase.

Suggested internal contracts (names illustrative):

```python
class OperationUsage(BaseModel):
    run_id: UUID | None
    stage: str
    provider: str
    operation: str
    model: str | None = None
    duration_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    embedding_tokens: int | None = None
    physical_attempts: int | None = None
    usage_source: Literal["provider", "estimated", "unavailable"]

class EvalCaseResult(BaseModel):
    case_id: str
    passed: bool | None
    metrics: dict[str, float | None]
    warnings: list[str]
```

Use typed models and bounded outputs; these are design examples, not a mandate to add persistent tables.

## 6. Non-functional constraints

- **Fail-open telemetry:** broken exporter/log sink cannot cause a research run to fail; log fallback is best effort.
- **No secret leakage:** enforce by tests capturing logs and span attributes.
- **Low overhead:** benchmark with tracing disabled/enabled; document measured impact. Avoid excessive synchronous exporting in hot paths.
- **Bounded memory:** do not retain unlimited operation records; cap in-process buffers and evaluation input/output sizes.
- **No new network in offline mode:** stub providers and prevent accidental live calls.
- **Privacy:** dataset contains synthetic or licensed/public test content, no real private PDFs or API keys.
- **Reproducibility:** fixture version, code SHA (when available), configuration/model versions, evaluation runner version, timestamps, seed (if relevant).

## 7. Acceptance criteria

1. A mocked research run produces correlated spans and JSON logs without raw prompt/evidence/API key content.
2. Disabling tracing requires no OTLP collector and leaves existing API behavior unchanged.
3. Logical GraphUsage remains backward-compatible and distinct from observed/estimated provider usage.
4. Unknown tokens, SDK retries or prices are marked unknown; partial cost is labeled partial.
5. Offline retrieval and citation scorers produce deterministic, versioned JSON and reproduce a committed baseline.
6. Optional judge runs only by explicit command and is checked against human-labeled cases.
7. Failure-injection tests cover provider/DB/Qdrant/timeouts/cancellation/SSRF/injection/resource caps, without network or paid keys.
8. No change to request-scoped SSE, terminal commit ordering, LangGraph graph topology, PDF storage policy or research-scope isolation.
9. Ruff, mypy, pytest and frontend regression checks (if frontend touched) pass; CI includes offline eval gate.
10. `docs/evaluation/methodology.md`, `baseline.md`, `failure-matrix.md` explain metrics, limitations and reproducibility.

## 8. Open questions to resolve in Step 1

- What exact LangChain structured-output response metadata survives the current wrappers? Prototype before committing token capture design.
- Which OpenTelemetry package versions are compatible with Python 3.14 and the pinned FastAPI/LangGraph stack? Resolve and lock versions during implementation.
- Is a per-run persisted usage summary needed for Phase 7 demo, or are logs/traces plus CLI artifacts sufficient? Default to no migration.
- What public/license-safe fixture corpus will be committed? Prefer tiny authored synthetic texts with stable IDs.
