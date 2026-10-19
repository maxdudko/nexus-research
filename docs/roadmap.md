# Development Roadmap

Nexus Research is planned as a seven-phase, approximately 14-day MVP build. The timeline is an estimate, not a commitment; each phase is complete only when its deliverables can be demonstrated and tested.

## Phase 1 — Foundation (Days 1–2)

**Goal:** Establish a maintainable Python backend and reproducible development environment.

- Initialize the Python project with `uv`, FastAPI, Pydantic settings, and a clear package structure.
- Configure PostgreSQL, SQLAlchemy async, Alembic migrations, and Qdrant via Docker Compose.
- Set up pytest, Ruff, mypy, environment configuration, and health checks.

**Deliverable:** A locally runnable, tested FastAPI service with working infrastructure and documented setup.

## Phase 2 — LLM & Tools (Days 3–4)

**Goal:** Connect OpenAI and implement the first reliable research tools.

- Integrate OpenAI through LangChain with validated structured outputs.
- Implement web search and page-content extraction tools.
- Add tool input validation, timeouts, retries, and basic usage tracking.

**Deliverable:** An API-level demonstration of structured LLM responses and real web-search tool calls.

## Phase 3 — RAG (Days 5–6)

**Goal:** Build a searchable evidence base from web pages and PDFs.

- Extract and normalize content from supported web pages and PDF documents.
- Chunk text, generate embeddings, and index content in Qdrant.
- Retrieve relevant passages with stable source metadata and research-level isolation.

**Deliverable:** A tested ingestion and retrieval pipeline returning relevant evidence with source references.

## Phase 4 — LangGraph (Days 7–9)

**Goal:** Connect tools and retrieval into an end-to-end research workflow.

- Implement planning, research, evidence retrieval, coverage checks, and report synthesis as graph nodes.
- Support conditional transitions, bounded retries, and execution state.
- Generate structured reports with source citations and explicit uncertainty.

**Deliverable:** `POST /api/v1/research/run` runs one question to a cited report through a bounded LangGraph workflow. PostgreSQL checkpoints record that state. A cancelled, timed-out, or crashed run is not resumed in the background.

## Phase 5 — Product UI (Days 10–11)

**Goal:** Make the workflow usable and demonstrable.

- Build a minimal Next.js research workspace with query input and PDF upload.
- Display research progress through server-sent events (SSE).
- Show research history, reports, and navigable source citations.

**Deliverable:** A usable web interface for launching and reviewing research runs.

## Phase 6 — Reliability (Days 12–13)

**Goal:** Evaluate research quality and handle common failures safely.

- Tracing, structured logs, and partial cost coverage are in the API. Tracing stays off unless `NEXUS_OTEL_ENABLED=true`.
- The offline evaluation suite scores frozen fixtures. Citation validity is separate from entailment. The live judge is manual.
- Failure behavior is indexed in `docs/evaluation/failure-matrix.md`. CI compares the offline artifact with `docs/evaluation/baseline.json`.

**Deliverable:** A repeatable evaluation suite and documented failure-handling behavior.

## Phase 7 — Release (Day 14)

**Goal:** Package a reproducible MVP demo.

- Finalize Docker-based local deployment and environment configuration.
- Document installation, configuration, architecture, limitations, and known issues.
- Prepare representative research examples and a short product demo.

**Deliverable:** A reproducible MVP that another developer can run and evaluate.

## MVP boundaries

**In scope:** OpenAI integration, web research, PDF RAG, bounded LangGraph orchestration, citations, research history, and a minimal UI.

**Out of scope:** Multi-user accounts, billing, scheduled research, distributed multi-agent swarms, enterprise integrations, and high-availability infrastructure.

## Definition of Done

The MVP is complete when a user can submit a research question, optionally provide PDF material, observe execution progress, and receive a saved report with verifiable source references. The workflow must have bounded execution, basic failure handling, and automated tests for its critical paths.
