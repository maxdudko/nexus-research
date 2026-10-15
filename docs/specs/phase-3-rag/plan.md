# Nexus Research — Phase 3 RAG Implementation Plan

**Status:** Ready for technical review; implementation after approval  
**Target:** Indicative roadmap Days 5–6 (learning and safety take precedence over dates)  
**Branch:** `development`  
**Workflow:** Cursor Plan → Build → Tests → Review in three groups  
**Dependencies:** Approved `spec.md` and `ADR-003.md`

## 0. Read-only baseline and design decisions

Read current files **before coding**:
```text
docs/roadmap.md
docs/adr/ADR-001.md
docs/adr/ADR-002.md
docs/specs/phase-2-llm-tools/spec.md
apps/api/pyproject.toml
apps/api/uv.lock
apps/api/.env.example
apps/api/src/nexus/main.py
apps/api/src/nexus/core/config.py
apps/api/src/nexus/api/v1/router.py
apps/api/src/nexus/api/errors.py
apps/api/src/nexus/infrastructure/database.py
apps/api/src/nexus/tools/web_search.py
apps/api/src/nexus/schemas/research.py
apps/api/alembic/env.py
docker-compose.yml
.github/workflows/ci.yml
```

Current baseline: Python 3.14, FastAPI, SQLAlchemy async, PostgreSQL 18, Qdrant 1.19.2, OpenAI via LangChain, Tavily, `create_app(settings)`, lifespan DB session factory, versioned API and green offline CI.

Before implementation, document decisions on:
1. Source identity/dedup and replacement semantics, including content changes.
2. HTTPX SSRF-safe transport including DNS rebinding and redirects; **do not ship an unsafe generic fetcher**.
3. Token-aware vs character-based chunking and precise units.
4. Qdrant point IDs, payload schema, versioned collection and embedding dimension.
5. Source status transitions and DB/Qdrant reconciliation.
6. Maximum input limits, per-request deadline and explicit cancellation behavior.

Run baseline:
```bash
cd apps/api
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m 'not integration'
```
Run full integration suite after Compose services start. Record existing failures separately.

## 1. Suggested file map

Adjust to repository conventions; do not create layers without responsibilities.

```text
apps/api/
  pyproject.toml                         # dependencies
  uv.lock                                # resolved lock
  .env.example                           # embedding/RAG limits
  alembic/env.py                         # target_metadata + model import
  alembic/versions/<revision>_sources.py # source table
  src/nexus/
    core/config.py                       # new RAG settings
    main.py                              # only necessary lifecycle wiring
    api/v1/router.py                     # evidence router
    api/v1/evidence.py                   # thin HTTP endpoints
    schemas/evidence.py                  # request/response contracts
    models/base.py                       # SQLAlchemy DeclarativeBase
    models/source.py                     # Source ORM model
    ingestion/service.py                 # lifecycle/orchestration
    ingestion/pdf.py                     # PyMuPDF extraction
    ingestion/web.py                     # safe HTTPX + Trafilatura
    ingestion/chunking.py                # deterministic chunks
    retrieval/embeddings.py              # async embedding adapter
    retrieval/vector_store.py            # Qdrant adapter
    retrieval/service.py                 # filtered retrieval
  tests/unit/test_evidence_schemas.py
  tests/unit/test_pdf_extraction.py
  tests/unit/test_safe_web.py
  tests/unit/test_chunking.py
  tests/unit/test_embeddings.py
  tests/unit/test_ingestion.py
  tests/unit/test_retrieval.py
  tests/integration/test_evidence_storage.py
  tests/integration/test_evidence_api.py
  tests/integration/test_embedding_live.py  # opt-in live
  README.md
  docs/learning/phase-3.md
```

Do not introduce LangGraph, a worker, Redis queue, PDF object storage, auth, UI or a general provider abstraction.

## Group A — Data foundation (Steps 1–2)

### Step 1 — PostgreSQL Source model and migration

**Goal:** Durable source identity, research scope and ingestion state.

Tasks:
1. Add `DeclarativeBase` and a minimal `Source` ORM model. Use UUID IDs and UTC timestamps; choose DB-side or application-side defaults consistently.
2. Define fields from spec, status enum strategy, constraints and indexes. Scope source identity by `research_id`; document how identical content and updated URLs are handled.
3. Define transition policy: PENDING → INDEXING → INDEXED or FAILED; FAILED → INDEXING on retry. Avoid marking INDEXED before vector upsert.
4. Update Alembic `target_metadata` from `None` to `Base.metadata`; ensure model imports are registered. Generate a reviewed migration; **do not blindly trust autogenerate**.
5. Ensure existing migration history remains intact. `upgrade head` twice must be safe.
6. Introduce repository functions only if they reduce duplication; keep transaction ownership explicit.
7. Handle concurrent claims for the same source using a uniqueness constraint and appropriate locking/transaction pattern.

Tests:
- Schema/migration on empty DB; repeated upgrade; uniqueness per research; state transitions; simultaneous claims; transaction rollback.
- Existing Phase 1 database tests still pass.

Acceptance:
- PostgreSQL source metadata persists and is isolated by research ID.
- No vector or external API required for this step.

Learn:
- SQLAlchemy `Mapped`, `mapped_column`, async sessions, `flush` vs `commit`, Alembic metadata; compare to Prisma/TypeORM.

Suggested commit: `feat(rag): add research-scoped source metadata`

### Step 2 — Embeddings and Qdrant adapter

**Goal:** Stable vector index and filtered search independent of HTTP endpoints.

Tasks:
1. Resolve Python 3.14-compatible `qdrant-client` and embedding dependencies; lock with uv.
2. Add `NEXUS_OPENAI_EMBEDDING_MODEL`, `NEXUS_RAG_COLLECTION`, batch size and timeout/limit settings.
3. Implement async embedding adapter with dependency injection for deterministic test doubles; keep API keys optional at startup.
4. Implement Qdrant adapter with async client, collection create/verify, Cosine distance, expected dimension, and `research_id` payload index.
5. Validate incompatible existing collection before writing; never drop/recreate automatically.
6. Upsert points with stable IDs and full provenance; provide delete-by-source/version and search with **mandatory Qdrant filter**.
7. Ensure client lifecycle closes resources. Use existing `NEXUS_QDRANT_URL`; avoid hidden globals.

Tests:
- Fake embedding dimension mismatch; batching; empty inputs; provider failure; collection mismatch; correct filter; stable IDs; Qdrant local integration.

Acceptance:
- Deterministic fake embeddings can index/search research-scoped chunks with zero paid API calls.

Learn:
- Embeddings vs LLM chat, vector dimensions, cosine similarity, payload filtering, Qdrant collections and point IDs.

Suggested commit: `feat(rag): add embedding and qdrant adapters`

**Group A review gate:** migration, schema and vector contract reviewed; Ruff/mypy/unit/integration tests pass. Confirm no collection destructive operations.

## Group B — Ingestion (Steps 3–4)

### Step 3 — PDF extraction, chunking and ingestion lifecycle

**Goal:** Real PDF → page-aware chunks → vectors + Source metadata.

Tasks:
1. Add `pymupdf` and `python-multipart`, verify installed API and Python 3.14 support.
2. Validate file size/signature/parseability, encryption, page count and extracted text. Read uploads in bounded chunks; reject oversized input before parsing.
3. Extract each page separately; preserve 1-based page number. Offload blocking extraction if required.
4. Implement deterministic whitespace normalization and chunking; choose tokenizer-aware units or explicitly character-based settings. Preserve page boundary.
5. Implement content hash, stable source ID, chunk IDs and deterministic point IDs. Handle empty chunks and chunk count cap.
6. Build ingestion coordinator with DB state transitions, bounded embedding batches, Qdrant upsert, cleanup and INDEXED commit only after success.
7. On failure record FAILED and enable retry; for replacement, ensure old version is not returned as current evidence.
8. Implement `POST /api/v1/evidence/pdf` using existing router/session dependency and error envelope.
9. Do not persist PDF bytes in database or filesystem. Explain rebuild/re-upload limitation.

Tests:
- Valid multi-page PDF; page provenance; invalid signature; encrypted/empty/image-only PDF; 413 limit; deterministic chunk IDs; repeated upload; failed embedding; failed upsert; cancellation/retry.
- Use generated tiny fixture PDFs or committed license-safe samples, not paid APIs.

Acceptance:
- Text PDF indexes and returns page-specific passages; repeat upload does not duplicate vectors.

Learn:
- Binary uploads, `UploadFile`, hashing, PyMuPDF resource cleanup, async vs blocking CPU work, consistency across DB and vector store.

Suggested commit: `feat(rag): add pdf ingestion`

### Step 4 — Safe web extraction and ingestion

**Goal:** Public web article → normalized text → chunks/vectors without SSRF exposure.

Tasks:
1. Add Trafilatura and verify compatibility. Use HTTPX async streaming for bounded HTML fetches.
2. Implement strict URL parser: HTTP(S) only, no credentials, public hosts only; disallow loopback/private/link-local/reserved IPs and suspicious host syntax.
3. Design/test DNS resolution and **connection pinning** so the actual TCP destination is validated. Recheck every redirect; disable unreviewed proxies and automatic unsafe redirects. A DNS precheck without pinning is not sufficient.
4. Limit redirects, timeouts, bytes, HTML content types and extraction length. Reject binary responses and oversized compressed/decompressed content.
5. Extract text/title using Trafilatura; normalize URL and metadata without trusting page instructions.
6. Reuse shared ingestion coordinator from Step 3. Avoid duplicating chunking, embedding and Qdrant write logic.
7. Implement `POST /api/v1/evidence/web`; no crawling or JS rendering.
8. If secure fetching cannot be proven with tests, **hold this endpoint** or use a constrained trusted extraction service after updating ADR/spec; do not ship a best-effort SSRF check.

Tests:
- IPv4/IPv6 private and public cases; localhost and localhost aliases; redirect to private address; public DNS resolving private IP; DNS rebinding between check and connect; proxy environment; missing Content-Length; oversized streamed response; non-HTML; redirect loops; malformed HTML; empty extraction; retry.
- Mock transport/resolver; no external network in CI.

Acceptance:
- Safe public HTML extracts with provenance; internal services cannot be targeted by crafted URLs.

Learn:
- HTTPX transport, DNS/SSRF, streaming, redirects, HTML extraction and trust boundaries.

Suggested commit: `feat(rag): add bounded safe web ingestion`

**Group B review gate:** focused security review, manual local ingestion tests, no unbounded fetch, no Phase 2 regression.

## Group C — Retrieval & verification (Steps 5–6)

### Step 5 — Research-scoped semantic retrieval

**Goal:** Evidence retrieval without answer synthesis.

Tasks:
1. Define typed request/response schemas and top-k bounds.
2. Embed query with the configured embedding adapter.
3. Search Qdrant using a server-side equality filter on `research_id` (never filter only after fetching).
4. Return stable chunk/source IDs, score, text, page number or URL; never invent missing provenance.
5. Filter out stale/failed versions according to chosen indexing strategy; validate that payload schema is complete.
6. Implement `POST /api/v1/evidence/search` as thin route. Keep no-sources result as `matches: []`.
7. Document that similarity is not factual confidence and Phase 4 owns grounded synthesis.

Tests:
- Relevant chunk ranking; top-k; empty collection; missing scope; cross-research leakage; stale source; malformed payload; Qdrant timeout; query embedding failure.

Acceptance:
- Same query scoped to two research IDs returns only each research's evidence.

Learn:
- Query embeddings, ANN retrieval, cosine scores, precision/recall, metadata filtering.

Suggested commit: `feat(rag): add research-scoped retrieval`

### Step 6 — Quality gate, evaluation and docs

**Goal:** Demonstrate correctness, isolation and basic retrieval quality.

Tasks:
1. Create at least 5 small text documents/PDFs and 10 manually labeled queries with expected source/page.
2. Evaluate Hit@5/Recall@5, inspect false positives and compare at least two chunk sizes. Keep fixtures deterministic and document results rather than claiming universal thresholds.
3. Add integration tests for migrations, PostgreSQL+Qdrant recovery and API contracts using fake embeddings.
4. Add opt-in live OpenAI embedding smoke test and a manual public-page fetch test; neither runs in default CI.
5. Verify provider credentials optional at startup, health routes unchanged, Phase 2 endpoints unchanged.
6. Update README, `.env.example`, architecture notes, learning journal and limitations.
7. Run full quality gate; inspect diff for leaked secrets and accidental schema changes.

Commands (from `apps/api`):
```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m 'not integration'
# with docker compose up -d from repository root:
uv run pytest
uv run alembic upgrade head
uv run alembic upgrade head
```

Manual smoke checklist:
- [ ] Index 2-page text PDF and retrieve a passage with page number.
- [ ] Index a safe public HTML article and retrieve a passage with URL.
- [ ] Index same content twice and observe no duplicate points.
- [ ] Update content and verify old vectors are not returned.
- [ ] Search with another research_id and receive no foreign evidence.
- [ ] Simulate Qdrant failure and recover on retry.
- [ ] Verify unsafe URL/redirect rejected before internal connection.
- [ ] Verify no OpenAI charges in default CI.

Suggested commit: `test(rag): verify retrieval and document phase 3`

**Group C review gate:** full PR review, manual smoke results, green CI, explicit limitations documented.

## 2. Sequencing and stop conditions

- **Stop after Step 2** if collection dimension/version or DB schema cannot be reconciled safely.
- **Stop before shipping Step 4** if SSRF-safe transport cannot be demonstrated.
- **Stop before merge** if research isolation fails, stale vectors are returned, or failures leave falsely INDEXED sources.
- Do not expand scope into LangGraph, PDF OCR, web crawling, report generation or UI to work around foundational issues.

## 3. Definition of Done

1. PDF and web ingestion return source IDs and index counts.
2. Retrieval returns real, scoped evidence with PDF page/web URL provenance.
3. Same-source retries and changed content are handled consistently.
4. Unsafe network targets are rejected; resource limits enforced.
5. Existing API behavior and CI remain green.
6. No paid calls in default CI; evaluation and manual smoke documented.
7. Phase 4 can call ingestion/retrieval services without importing FastAPI route handlers.

## 4. Cursor prompts

### Group A — Plan only
> Read `docs/specs/phase-3-rag/spec.md`, `docs/specs/phase-3-rag/plan.md`, `docs/adr/ADR-003.md`, ADR-001/002, and inspect current development branch. Propose an implementation plan for Steps 1–2 only: SQLAlchemy Source/Alembic metadata, async embeddings, Qdrant schema and mandatory research filter. Identify exact files, failure/retry semantics and test commands. Do not modify files until approved. Explain new Python patterns compared with TypeScript/NestJS.

### Group B — Plan only
> Review completed Phase 3 Group A and approved spec/ADR. Plan Steps 3–4 only: page-aware PDF ingestion and SSRF-safe HTTPX+Trafilatura web ingestion using a shared coordinator. Explicitly prove how DNS rebinding and redirects are blocked, how bytes/time are bounded and how partial DB/Qdrant failures recover. If a safe fetcher cannot be implemented, recommend a documented scope reduction. Do not edit code until approved.

### Group C — Plan only
> Review completed Phase 3 Groups A/B and approved spec/ADR. Plan Steps 5–6 only: scoped semantic retrieval, citation metadata, negative tests, offline evaluation dataset, live smoke and quality gate. Check for stale-vector leakage and regressions in Phase 1/2. Do not modify code until approved.

## 5. Learning checklist

- [ ] Explain `Mapped`/`mapped_column`, session commit/rollback and Alembic autogeneration.
- [ ] Explain why Qdrant points and PostgreSQL rows cannot share a transaction.
- [ ] Explain embedding dimensions, cosine similarity and top-k.
- [ ] Explain chunk overlap and why PDF page boundaries matter.
- [ ] Explain async I/O vs CPU-bound extraction.
- [ ] Explain SSRF, DNS rebinding and redirect validation.
- [ ] Explain why research_id is a retrieval filter, not authorization.
- [ ] Explain retrieval evaluation separately from LLM answer quality.
