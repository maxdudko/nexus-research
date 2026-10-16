# Nexus Research — Phase 3: RAG Specification

**Status:** Proposed — awaiting approval  
**Version:** 1.0  
**Date:** 2026-10-15  
**Target branch:** `development`  
**Prerequisites:** Phase 1 infrastructure; Phase 2 OpenAI/Tavily research tools  
**Decisions:** HTTPX + Trafilatura web extraction; PostgreSQL metadata; Qdrant vectors; OpenAI embeddings.

## 1. Context

Phase 2 exposes `/api/v1/research/plan` and `/api/v1/research/discover`, but discovery operates primarily on Tavily snippets. Phase 3 adds a reusable **evidence subsystem** for PDF and web ingestion, embedding, storage and research-scoped semantic retrieval. Phase 4 LangGraph will orchestrate these capabilities; Phase 5 will add the product UI.

Existing baseline on `development`:
- Python `>=3.14,<3.15`, `uv`, FastAPI, Pydantic v2.
- SQLAlchemy 2 async, `asyncpg`, Alembic, PostgreSQL 18.
- Qdrant 1.19.2 in root `docker-compose.yml`.
- `create_app(settings)` and lifespan-managed DB engine/session factory.
- Existing `nexus.api.v1.router`, `nexus.infrastructure.database.get_session`, error envelope and CI.
- `langchain-openai` for chat, Tavily SDK for discovery; optional credentials at startup.

## 2. Outcomes

1. Ingest text-based PDF files and safe, publicly accessible HTTP(S) HTML pages.
2. Preserve stable source and passage provenance: research ID, source ID, chunk ID, URL/page number and text.
3. Embed passages with a separately configurable OpenAI embedding model.
4. Store source lifecycle metadata in PostgreSQL and indexed vectors/payload in Qdrant.
5. Retrieve relevant passages by semantic similarity with **mandatory research isolation**.
6. Support repeatable indexing, predictable partial-failure recovery and automated offline tests.
7. Expose thin API endpoints for ingestion and retrieval; **do not** generate final research reports.

## 3. Functional requirements

### FR-01 — Configuration and dependencies
- Add and lock compatible versions of `qdrant-client`, `PyMuPDF` (distribution `pymupdf`), `trafilatura`, `python-multipart`, and a tokenizer/splitter dependency only if actually used. `httpx` and `langchain-openai` already exist.
- Verify Python 3.14 compatibility with the installed versions; do not guess APIs or package versions.
- Add optional `NEXUS_OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`), `NEXUS_RAG_COLLECTION` (e.g. `nexus_evidence_v1`), positive extraction/embedding timeouts and finite ingestion limits.
- Treat vector dimensions as part of the index contract: `text-embedding-3-small` normally yields 1536 dimensions unless configured otherwise. Reject mismatches; never silently reuse a collection with incompatible dimensions/distance.
- Keys remain optional at startup; `/health/live` and `/health/ready` remain independent of embedding configuration. Fail ingestion/retrieval safely if a required provider is unavailable.
- Do not modify Phase 2 `NEXUS_OPENAI_MODEL` chat-model semantics.

### FR-02 — Source identity and persistence
- `research_id: UUID` is supplied by the caller for now; Phase 4 owns durable research-run creation. Do not imply authentication or tenancy is implemented.
- PostgreSQL `sources` table: `id UUID PK`, `research_id UUID NOT NULL`, `type WEB|PDF`, `uri nullable` (web URL or internal PDF reference, **never raw filesystem path**), `display_name`, `content_hash`, `status PENDING|INDEXING|INDEXED|FAILED`, `error_code nullable`, `chunk_count`, `created_at`, `updated_at`. Add a deliberate uniqueness strategy scoped by `research_id` and canonical source identity; document same URL with changed content.
- Qdrant point payload includes `research_id`, `source_id`, `chunk_id`, `chunk_index`, `text`, `page_number nullable`, `url nullable`, `title nullable`. Use a **single collection** for MVP with a payload index on `research_id`.
- Source is marked INDEXED only after Qdrant upsert succeeds. Reconcile DB/Qdrant drift on retry; avoid duplicate points using deterministic IDs derived from source identity, content/version and chunk index.
- Define replacement semantics: when source content changes, old vectors must not remain retrievable as current evidence. Prefer staged upsert + cleanup and explicit FAILED/retry handling; no false claim of cross-store atomicity.
- Ingestion operations for the same research/source must be serialized or protected by a DB uniqueness/claim mechanism. Concurrent retries must not race into inconsistent states.

### FR-03 — PDF extraction
- Accept `multipart/form-data`: `research_id` and `file`. Only PDF files; validate bytes/signature and parseability rather than trusting filename or content type.
- Stream/read with a strict byte limit (suggest 10 MiB), page limit (suggest 100) and extracted-text limit; reject empty, encrypted, malformed or image-only PDFs with sanitized errors.
- Extract text **per page** using PyMuPDF; preserve 1-based `page_number`. Do not merge chunks across page boundaries if that would lose unambiguous citation location.
- Close documents/resources on success, error and cancellation. No OCR or table/image understanding in this phase.
- Do not persist original uploaded file in Phase 3. Record a stable internal PDF reference and content hash; document that later reprocessing requires re-upload unless durable object storage is introduced.

### FR-04 — Safe web extraction
- Accept `research_id` and a public HTTP(S) URL. Use HTTPX for fetching and Trafilatura for HTML-to-text extraction.
- **SSRF prevention is mandatory:** reject credentials in URLs, non-HTTP(S) schemes, localhost, loopback, private/link-local/reserved/multicast IPs, IPv6 equivalents and non-public DNS resolutions. Revalidate each redirect; limit redirects (e.g. 3). Avoid DNS rebinding by binding connection to validated addresses or using an equivalent transport-level enforcement; a pre-request DNS check alone is insufficient.
- No unrestricted proxy use (`trust_env=False` unless explicitly reviewed), no user-controlled arbitrary headers, no access to internal services. Prefer HTTPS; document handling of plain HTTP.
- Enforce connect/read/total timeouts, max response bytes (suggest 2 MiB), HTML content-type checks, charset handling, and no JavaScript execution. Stream response to enforce byte cap even when Content-Length is absent.
- Preserve final canonical public URL and extracted title when available. Treat extracted text as **untrusted content**, not executable instructions.
- Extraction failures, unsupported content, non-public URLs and empty text return predictable sanitized errors.
- If safe transport-level DNS/IP pinning is not achievable in this phase, **do not ship arbitrary URL fetching**: use a restricted trusted extraction path and document the limitation.

### FR-05 — Normalization and chunking
- Normalize whitespace without destroying paragraph meaning; avoid silently changing factual content.
- Implement deterministic chunking with overlap; initial experiment: ~800 tokens/chunk and ~120-token overlap, using an actual tokenizer if claiming token counts. If character-based, name and configure values as characters instead.
- Preserve PDF page numbers and web source URLs; maintain `chunk_index`, `source_id`, `research_id` for every passage.
- Skip empty chunks; cap total chunks per source (suggest 200) and total input tokens/embedding batch sizes. Repeated ingestion of identical content must be idempotent.
- Record extraction/chunking algorithm version or incorporate it into the index version so changes can trigger reindex.

### FR-06 — Embeddings and Qdrant
- Use OpenAI embeddings via an async interface (`OpenAIEmbeddings` or verified compatible SDK); configure separately from chat model. Batch boundedly; retry transient failures with capped attempts.
- Create/verify Qdrant collection with Cosine distance, correct vector size and payload index. Do not recreate/delete a mismatched existing collection automatically.
- Upsert stable point IDs and metadata; use mandatory server-side `research_id` filter on every retrieval query. Never retrieve globally and filter only in Python.
- Return top-k (default 5; bounded e.g. 1–10) with `score`, `text`, `chunk_id`, `source_id`, `research_id`, `page_number` and `url`.
- Retrieval must not invoke a chat LLM or invent answers. Qdrant similarity scores are not probabilities of correctness.

### FR-07 — HTTP contracts
Use the existing `/api/v1` router and sanitized `{ "error": { "code", "message" } }` envelope.

**`POST /api/v1/evidence/pdf`** (`multipart/form-data`)
- Fields: `research_id` UUID, `file` PDF.
- Success `200` or `201`: `{"source_id":"<uuid>","research_id":"<uuid>","status":"indexed","chunk_count":12,"deduplicated":false}`.
- Repeat of identical source/content returns existing source identity with `deduplicated:true`; document retry behavior if status FAILED.

**`POST /api/v1/evidence/web`**
```json
{"research_id":"a8c11b5e-fd31-4c24-9d80-132b317fa7f5","url":"https://example.org/article"}
```
- Same ingestion response shape as PDF. Reject unsafe URLs before any network connection.

**`POST /api/v1/evidence/search`**
```json
{"research_id":"a8c11b5e-fd31-4c24-9d80-132b317fa7f5","query":"What limits AI coding agents?","top_k":5}
```
Response:
```json
{
  "matches": [
    {
      "chunk_id":"<uuid>",
      "source_id":"<uuid>",
      "research_id":"a8c11b5e-fd31-4c24-9d80-132b317fa7f5",
      "score":0.82,
      "text":"Example retrieved passage, not a model-generated answer.",
      "page_number":12,
      "url":null
    }
  ]
}
```
- This example is illustrative; score is not calibrated confidence.
- Validation errors `422`, oversized inputs `413`, unsupported PDF/media `415`, unsafe URL `422`, provider/index failures `503` or `504` according to existing error mapping. Never echo secret URLs, credentials, full document content or stack traces.
- Ingestion may run synchronously **only within strict time and size limits**. No job queue, polling or SSE yet.

### FR-08 — Tests and evaluation
- Unit tests for URL/redirect/IP validation (IPv4/IPv6, DNS rebinding), PDF extraction/page numbers, chunk overlap, deterministic IDs, content hashing, metadata schemas, provider errors and idempotency.
- Integration tests against existing Compose PostgreSQL + Qdrant: migrations, collection configuration, insert/retry/replace, filter isolation, failed Qdrant upsert and recovery.
- Mock OpenAI embeddings in default CI; no paid calls. Optional opt-in live embedding smoke test.
- Small manually labeled retrieval fixture (at least 5 documents, 10 queries) with expected relevant source/page; report Recall@5 or Hit@5 and inspect failures. Do not promise a universal quality threshold before establishing baseline.
- Preserve existing Phase 1/2 tests, CI, health endpoints and code quality checks.

## 4. Nonfunctional requirements

- Async I/O; no event-loop blocking for CPU-heavy PDF extraction (offload to thread if needed).
- Bound memory, bytes, pages, chunks, tokens, provider retries, redirect count and wall-clock execution.
- Deterministic outputs for extraction/chunking where feasible; stable metadata and explicit versioning.
- No raw user PDF or web body logging; no secrets in errors.
- No auth/tenancy: `research_id` is a logical isolation filter, **not an access-control boundary**. Do not expose the API publicly as a multi-user service without authentication/authorization.
- All externally fetched text is untrusted; Phase 4 must retain prompt-injection defenses.

## 5. Out of scope

OCR, scanned PDF reconstruction, tables/images, Playwright, site crawling, hybrid search, reranking, RAG answer generation, LangGraph, report synthesis, UI, durable background jobs, object storage, user auth, multi-provider embeddings and distributed indexing.

## 6. Acceptance criteria / Definition of Done

- [ ] Migrations create a durable sources table and pass twice.
- [ ] Qdrant collection and indexes are validated; vector-size mismatch fails safely.
- [ ] A real text PDF can be indexed and retrieved with correct page references.
- [ ] A safe public HTML page can be extracted and indexed with URL provenance.
- [ ] Repeated same-content ingestion is idempotent; changed content replaces old evidence.
- [ ] Failure between DB and Qdrant is recoverable; no falsely INDEXED source.
- [ ] Every retrieval uses server-side research_id filtering; cross-research results are absent.
- [ ] Unsafe URLs, redirects and DNS rebinding are blocked; byte/page/time limits enforced.
- [ ] Unit/integration tests, Ruff, mypy, Alembic and existing Phase 1/2 tests pass.
- [ ] No live API charges in default CI; manual smoke test documented.
- [ ] API returns evidence only, with no fabricated report or grounding claims.

## 7. Open questions (resolve in Step 0)

1. PDF identity: content-hash-only per research vs client-supplied document key for replace semantics.
2. Safe HTTPX transport: concrete DNS pinning implementation or a constrained alternative; no unprotected generic fetcher.
3. Chunk units: tokenizer-aware tokens or explicitly character-based splitting.
4. Index replacement/reconciliation policy and Qdrant collection schema version.
5. Whether to introduce a lightweight `research_scopes` table now (default: no, UUID only).
6. Whether request-scoped ingestion is fast enough under proposed limits; if not, reduce limits rather than prematurely introducing workers.

## 8. References

- `docs/roadmap.md`
- `docs/adr/ADR-001.md`, `docs/adr/ADR-002.md`
- `docs/specs/phase-2-llm-tools/spec.md`
- Qdrant Python client: https://github.com/qdrant/qdrant-client
- PyMuPDF: https://pymupdf.readthedocs.io/
- Trafilatura: https://trafilatura.readthedocs.io/
- HTTPX: https://www.python-httpx.org/
- OpenAI embeddings: https://platform.openai.com/docs/guides/embeddings
