# Development Roadmap

Nexus Research was planned as a seven-phase, approximately 14-day MVP build, followed by a separate Alpha milestone. The timeline is an estimate, not a commitment; each phase is complete only when its deliverables can be demonstrated and tested.

| Phase | Status |
|---|---|
| 1. Foundation | Done |
| 2. LLM & Tools | Done |
| 3. RAG | Done |
| 4. LangGraph | Done |
| 5. Product UI | Done |
| 6. Reliability | Done |
| 7. Release | Release candidate. Packaging, smoke and documentation are on `development`; complete only after Gate C in [`release/checklist.md`](release/checklist.md) |
| 8–10, Alpha `v0.2.0` | Not started; out of MVP scope |

## Phase 1 — Foundation (Days 1–2)

**Status:** Done.

**Goal:** Establish a maintainable Python backend and reproducible development environment.

- Initialize the Python project with `uv`, FastAPI, Pydantic settings, and a clear package structure.
- Configure PostgreSQL, SQLAlchemy async, Alembic migrations, and Qdrant via Docker Compose.
- Set up pytest, Ruff, mypy, environment configuration, and health checks.

**Deliverable:** A locally runnable, tested FastAPI service with working infrastructure and documented setup.

## Phase 2 — LLM & Tools (Days 3–4)

**Status:** Done.

**Goal:** Connect OpenAI and implement the first reliable research tools.

- Integrate OpenAI through LangChain with validated structured outputs.
- Implement web search and page-content extraction tools.
- Add tool input validation, timeouts, retries, and basic usage tracking.

**Deliverable:** An API-level demonstration of structured LLM responses and real web-search tool calls.

## Phase 3 — RAG (Days 5–6)

**Status:** Done.

**Goal:** Build a searchable evidence base from web pages and PDFs.

- Extract and normalize content from supported web pages and PDF documents.
- Chunk text, generate embeddings, and index content in Qdrant.
- Retrieve relevant passages with stable source metadata and research-level isolation.

**Deliverable:** A tested ingestion and retrieval pipeline returning relevant evidence with source references.

## Phase 4 — LangGraph (Days 7–9)

**Status:** Done.

**Goal:** Connect tools and retrieval into an end-to-end research workflow.

- Implement planning, research, evidence retrieval, coverage checks, and report synthesis as graph nodes.
- Support conditional transitions, bounded retries, and execution state.
- Generate structured reports with source citations and explicit uncertainty.

**Deliverable:** `POST /api/v1/research/run` runs one question to a cited report through a bounded LangGraph workflow. PostgreSQL checkpoints record that state. A cancelled, timed-out, or crashed run is not resumed in the background.

## Phase 5 — Product UI (Days 10–11)

**Status:** Done.

**Goal:** Make the workflow usable and demonstrable.

- Build a minimal Next.js research workspace with query input and PDF upload.
- Display research progress through server-sent events (SSE).
- Show research history, reports, and navigable source citations.

**Deliverable:** A usable web interface for launching and reviewing research runs.

## Phase 6 — Reliability (Days 12–13)

**Status:** Done.

**Goal:** Evaluate research quality and handle common failures safely.

- Tracing, structured logs, and partial cost coverage are in the API. Tracing stays off unless `NEXUS_OTEL_ENABLED=true`.
- The offline evaluation suite scores frozen fixtures. Citation validity is separate from entailment. The live judge is manual.
- Failure behavior is indexed in `docs/evaluation/failure-matrix.md`. CI compares the offline artifact with `docs/evaluation/baseline.json`.

**Deliverable:** A repeatable evaluation suite and documented failure-handling behavior.

## Phase 7 — Release (Day 14)

**Status:** Release candidate. Phase 7 is complete, and `v0.1.0` is tagged, only when every blocking criterion in [`release/checklist.md`](release/checklist.md) has recorded evidence. The release is *demonstrated* only after the manual paid-provider demo (AC-10).

**Goal:** Package a reproducible MVP demo.

- Docker-based deployment: non-root API and Web images, the private `compose.release.yml` stack with one-shot schema initialization, and a packaged smoke test in CI. See [`release/deployment.md`](release/deployment.md).
- Installation, configuration, architecture, operations, limitations and known issues are documented in the README and [`release/`](release/).
- Representative research scenarios and a recording template are in [`release/demo.md`](release/demo.md).

**Deliverable:** A reproducible MVP that another developer can run and evaluate.

## MVP boundaries

**In scope:** OpenAI integration, web research, PDF RAG, bounded LangGraph orchestration, citations, research history, and a minimal UI.

**Out of scope:** Multi-user accounts, billing, scheduled research, distributed multi-agent swarms, enterprise integrations, and high-availability infrastructure.

## Definition of Done

The MVP is complete when a user can submit a research question, optionally provide PDF material, observe execution progress, and receive a saved report with verifiable source references. The workflow must have bounded execution, basic failure handling, and automated tests for its critical paths.

## After the MVP: Alpha `v0.2.0`

These phases follow the tagged `v0.1.0` release. None of them is part of the MVP or required to pass Phase 7. Each will get its own specification before work starts.

- **Phase 8 — Identity and tenancy:** authentication, user-owned research data, and authorization enforced across PostgreSQL, Qdrant, PDF ingestion, history and SSE.
- **Phase 9 — Research UX:** research workspace redesign, user dashboard and onboarding.
- **Phase 10 — Admin and evaluation:** a restricted admin and evaluation UI, run comparisons and usage analytics.
- **Alpha `v0.2.0`:** controlled user onboarding, a landing page and product validation.

Until Phase 8 ships, the limits in [`release/limitations.md`](release/limitations.md) apply: no authentication, no per-user isolation, and no public deployment.
