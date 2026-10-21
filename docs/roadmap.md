# Development Roadmap

Nexus Research started as a seven-phase, approximately 14-day MVP build. The tagged [`v0.1.0`](https://github.com/maxdudko/nexus-research/releases/tag/v0.1.0) completes that milestone. Alpha `v0.2.0` is the next, **separately scoped engineering milestone**: evolve the research engine into a controlled multi-user application without assuming a validated commercial niche.

The MVP day estimates are historical planning labels, not a timetable for Alpha. Phase 8–10 durations will be set after specifications and dependency reviews. Each phase is complete only when its deliverable is demonstrated and tested.

| Phase | Status |
|---|---|
| 1. Foundation | Done — MVP `v0.1.0` |
| 2. LLM & Tools | Done — MVP `v0.1.0` |
| 3. RAG | Done — MVP `v0.1.0` |
| 4. LangGraph | Done — MVP `v0.1.0` |
| 5. Product UI | Done — MVP `v0.1.0` |
| 6. Reliability | Done — MVP `v0.1.0` |
| 7. Release | Done — MVP `v0.1.0` |
| 8. Identity & Multi-User | Proposed — Alpha `v0.2.0` |
| 9. Product Experience | Proposed — Alpha `v0.2.0` |
| 10. Admin & Evaluation | Proposed — Alpha `v0.2.0` |
| Alpha `v0.2.0` Release | Not started |

## MVP v0.1.0 — Completed

## Phase 1 — Foundation

**Status:** Done.

**Goal:** Establish a maintainable Python backend and reproducible development environment.

- Initialize the Python project with `uv`, FastAPI, Pydantic settings, and a clear package structure.
- Configure PostgreSQL, SQLAlchemy async, Alembic migrations, and Qdrant via Docker Compose.
- Set up pytest, Ruff, mypy, environment configuration, and health checks.

**Deliverable:** A locally runnable, tested FastAPI service with working infrastructure and documented setup.

## Phase 2 — LLM & Tools

**Status:** Done.

**Goal:** Connect OpenAI and implement the first reliable research tools.

- Integrate OpenAI through LangChain with validated structured outputs.
- Implement web search and page-content extraction tools.
- Add tool input validation, timeouts, retries, and basic usage tracking.

**Deliverable:** An API-level demonstration of structured LLM responses and real web-search tool calls.

## Phase 3 — RAG

**Status:** Done.

**Goal:** Build a searchable evidence base from web pages and PDFs.

- Extract and normalize content from supported web pages and PDF documents.
- Chunk text, generate embeddings, and index content in Qdrant.
- Retrieve relevant passages with stable source metadata and research-level isolation.

**Deliverable:** A tested ingestion and retrieval pipeline returning relevant evidence with source references.

## Phase 4 — LangGraph

**Status:** Done.

**Goal:** Connect tools and retrieval into an end-to-end research workflow.

- Implement planning, research, evidence retrieval, coverage checks, and report synthesis as graph nodes.
- Support conditional transitions, bounded retries, and execution state.
- Generate structured reports with source citations and explicit uncertainty.

**Deliverable:** `POST /api/v1/research/run` runs one question to a cited report through a bounded LangGraph workflow. PostgreSQL checkpoints record that state. A cancelled, timed-out, or crashed run is not resumed in the background.

## Phase 5 — Product UI

**Status:** Done.

**Goal:** Make the workflow usable and demonstrable.

- Build a minimal Next.js research workspace with query input and PDF upload.
- Display research progress through server-sent events (SSE).
- Show research history, reports, and navigable source citations.

**Deliverable:** A usable web interface for launching and reviewing research runs.

## Phase 6 — Reliability & Evaluation

**Status:** Done.

**Goal:** Evaluate research quality and handle common failures safely.

- Tracing, structured logs, and partial cost coverage are in the API. Tracing stays off unless `NEXUS_OTEL_ENABLED=true`.
- The offline evaluation suite scores frozen fixtures. Citation validity is separate from entailment. The live judge is manual.
- Failure behavior is indexed in `docs/evaluation/failure-matrix.md`. CI compares the offline artifact with `docs/evaluation/baseline.json`.

**Deliverable:** A repeatable evaluation suite and documented failure-handling behavior.

## Phase 7 — Release

**Status:** Done. Gate C evidence, including the paid-provider demo, is recorded in [`release/checklist.md`](release/checklist.md). `v0.1.0` is the annotated tag on that release commit.

**Goal:** Package a reproducible MVP demo.

- Docker-based deployment: non-root API and Web images, the private `compose.release.yml` stack with one-shot schema initialization, and a packaged smoke test in CI. See [`release/deployment.md`](release/deployment.md).
- Installation, configuration, architecture, operations, limitations and known issues are documented in the README and [`release/`](release/).
- Representative research scenarios and a recording template are in [`release/demo.md`](release/demo.md).

**Deliverable:** A reproducible MVP that another developer can run and evaluate.

## MVP boundaries

**In scope:** OpenAI integration, web research, PDF RAG, bounded LangGraph orchestration, citations, research history, and a minimal UI.

**Out of scope:** Multi-user accounts, billing, scheduled research, distributed multi-agent swarms, enterprise integrations, and high-availability infrastructure.

## MVP Definition of Done

The MVP is complete when a user can submit a research question, optionally provide PDF material, observe execution progress, and receive a saved report with verifiable source references. The workflow must have bounded execution, basic failure handling, and automated tests for its critical paths.

---

## Alpha v0.2.0 — Proposed

**Vision:** Turn the private single-operator research MVP into a controlled, multi-user research application with a clear user experience and restricted operator diagnostics. Preserve a reusable research core. Product & Market Intelligence is a candidate first specialization, not a prerequisite for Alpha.

**Vision document:** [`alpha-v0.2.0-vision`](specs/alpha-v0.2.0-vision.md).

**Execution:** each phase receives a separate `spec.md`, ADR(s) and `plan.md` before implementation. Use Plan → Learn → Implement → Tests → Review where hands-on Python work improves understanding. Phase 8 is the security prerequisite for exposing multi-user functionality; phases 9 and 10 build on its contracts.

## Phase 8 — Identity & Multi-User

**Status:** Proposed. Implementation not started; decisions require a phase specification and ADR.

**Goal:** Introduce authenticated users and ownership-aware authorization without exposing one user's research, reports or evidence to another.

- Design a minimal user identity and controlled onboarding model with secure login, logout and session lifecycle; decide credentials/session implementation in an ADR rather than in this roadmap.
- Introduce user ownership for research runs, reports, sources and evidence; define a safe migration policy for pre-Alpha data without owners.
- Enforce server-side authorization through research execution, PDF ingestion, history and report endpoints, SSE, PostgreSQL queries, Qdrant retrieval and LangGraph checkpoint identifiers. A `research_id` must never function as a permission token.
- Add basic user/admin permissions and protect administrative entry points; review rate limits, concurrent research quotas and operator-funded provider usage before any invited-user deployment.
- Add migrations, negative isolation tests (IDOR/BOLA), concurrent multi-account integration tests and a focused security review. Preserve existing research behavior where possible.

**Deliverable:** Two invited users can sign in, launch research and revisit their own results independently, with verified API and data-store isolation. Existing MVP records have an explicit safe migration/ownership disposition.

## Phase 9 — Product Experience

**Status:** Proposed. Depends on Phase 8 identity and user-scoped API contracts.

**Goal:** Make Nexus Research understandable and usable by an invited non-technical user without setup instructions or knowledge of the underlying agent.

- Build a responsive application shell with navigation, account/session controls and a user-owned research dashboard.
- Refine the question/PDF workflow, progress and heartbeat feedback, cancellation, terminal/error states and readable reports with citations and uncertainty indicators.
- Provide discoverable history, report reopening, consistent empty/loading/error states and clear behavior for partial or interrupted research.
- Add lightweight onboarding, accessibility and usability verification; maintain a general-purpose research interface while market positioning is under investigation.
- Keep design decisions independent from speculative domain-specific workflows; do not require project folders, sharing or competitor tracking to ship Alpha.

**Deliverable:** An invited user can sign in, start a web/PDF research task, follow progress, understand the outcome, and later find their own report using a coherent, responsive interface.

## Phase 10 — Admin & Evaluation

**Status:** Proposed. Depends on Phase 8 admin authorization; can be planned alongside Phase 9 after contracts are stable.

**Goal:** Give the system operator secure visibility into AI evaluation, research run health and approximate usage without overstating factual accuracy or exposing private user data.

- Implement restricted administrator API/UI access and privacy-conscious diagnostic views with an explicit data access policy.
- Persist versioned evaluation run metadata and results: dataset and prompt/model version, configuration, timestamps and case outcomes; maintain provenance for comparisons.
- Compare compatible evaluation runs against the committed offline baseline; show confusion matrices and failures when labeled data supports them. Keep offline fixture regression, informational live judge and actual end-to-end quality distinct.
- Provide basic run diagnostics, error summaries and usage/cost estimates with explicit coverage and unknown fields; do not present estimates as provider invoices.
- Add authorization, persistence, version-compatibility, metric calculation and UI tests. Secure any live evaluation execution behind explicit opt-in and bounded budgets if it is included at all.

**Deliverable:** A platform administrator can inspect historical evaluation results, compare compatible runs and troubleshoot research behavior, while standard users cannot access administrative resources or private diagnostic data.

## Alpha v0.2.0 — Integration & Release

**Status:** Not started. Requires reviewed Phase 8–10 evidence.

**Goal:** Validate that the new application layers work together without regressing the MVP research engine or compromising user isolation.

- Run a documented two-account end-to-end test (web research, PDF/evidence, stream cancellation, history and report access), including negative authorization cases.
- Verify migration from a tagged MVP-style data set, schema/checkpoint compatibility, clean installation and upgrade, and persistence after restart.
- Preserve offline evaluation regression gates and packaged smoke; improve CI cost and duration without weakening pre-merge or release security checks.
- Verify admin authorization, privacy-aware logs, run diagnostics, basic usage limits, secrets and controlled deployment configuration.
- Complete usability checks with invited users, update release documentation and limitations, and publish `v0.2.0` only after a reviewed GO decision.

**Deliverable:** A reproducible, controlled multi-user Alpha suitable for limited invitation-only evaluation, with recorded isolation, UX, administration and operational evidence.

## Alpha boundaries

**Core:** identity and sessions, user ownership and isolation, research UX/dashboard/onboarding, restricted admin views, evaluation history/comparison, security and integration verification.

**Stretch:** richer usage-cost analysis, lightweight export, user preferences, research organization and additional UX polish — only after core acceptance criteria pass.

**Out of scope by default:** public anonymous SaaS, paid billing, organizations/team sharing, enterprise RBAC/SSO, background job queues or automatic research resume, scheduled monitoring, large multi-model refactors, full document library/OCR, domain-specific Product & Market Intelligence features, and high availability.

## Alpha Definition of Done

The Alpha is complete when multiple invited users can authenticate and independently conduct research with verified ownership and evidence isolation; an invited user can complete the normal research journey without engineering assistance; an administrator can safely inspect versioned evaluation results and basic diagnostics; migrations and existing MVP regression tests pass; and the release has documented private deployment, known limitations and a reviewed tag.

A successful commercial validation, paid customers and public production launch are **not** mandatory for `v0.2.0`. The market research track runs in parallel and may shape future specialized research workflows.

## Parallel Product Discovery

**Working direction:** a general-purpose research core with Product & Market Intelligence as the initial candidate specialization. Investigate ICP, Jobs-to-be-Done, competing workflows and willingness to pay with real users. Record findings separately from engineering specifications. Do not convert untested hypotheses into compulsory Alpha functionality.

**Next decision:** write and review the Phase 8 identity/ownership specification and ADR before modifying storage schemas or adding authentication dependencies. Until the Phase 8 security gate passes, the limitations in [`release/limitations.md`](release/limitations.md) apply; do not publicly expose the existing anonymous application.
