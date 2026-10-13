# Nexus Research — Phase 2: LLM & Tools

**Status:** Proposed — awaiting approval  
**Version:** 1.0  
**Date:** 2026-10-13  
**Target branch:** `development`  
**Prerequisite:** Phase 1 foundation and green CI  
**Scope:** OpenAI integration, structured planning, Tavily web search, bounded manual tool calling

## 1. Context

Nexus Research is a Python-first research assistant. The complete MVP will combine web research, PDF ingestion, RAG, LangGraph orchestration and a Next.js UI. Phase 1 established FastAPI, Pydantic Settings, SQLAlchemy async, Alembic, PostgreSQL, Qdrant, tests and CI. Phase 2 adds a narrow LLM/tool vertical slice **without** implementing LangGraph, retrieval or document processing.

Decisions confirmed by the project owner:

- **LLM provider:** OpenAI only.
- **Model strategy:** one configurable model, `NEXUS_OPENAI_MODEL`.
- **Web search provider:** Tavily, using its search API, not Tavily Research API.
- **LLM runtime:** `langchain-openai` / LangChain model and tool primitives; no LangChain agent executor.
- **Execution:** async and request-scoped for Phase 2; no persistence or background worker.

## 2. Goals and outcomes

1. Demonstrate a real asynchronous OpenAI call through LangChain.
2. Produce a typed, validated research plan using Pydantic structured output.
3. Search the web with Tavily and normalize source metadata.
4. Execute model-requested `web_search` calls in a **manual, bounded** tool loop.
5. Expose testable HTTP endpoints while preserving the existing FastAPI architecture.
6. Learn the distinction between structured outputs, tool schemas, tool execution, and agent orchestration.

**Phase 2 success:** A user can submit a question and receive (a) a structured plan or (b) a bounded discovery result containing real Tavily sources and a short source-grounded discovery summary. It is not a finished research report.

## 3. Functional requirements

### FR-01 — Configuration and dependencies

- Add `langchain-openai` and the official Tavily Python SDK (`tavily-python` distribution); resolve compatible versions with uv and commit the updated `uv.lock`.
- Retain Python `>=3.14,<3.15`, the existing `src/nexus` layout, Ruff, mypy strict, pytest, and CI.
- Reuse `NEXUS_OPENAI_API_KEY` from Phase 1; add `NEXUS_OPENAI_MODEL` and `NEXUS_TAVILY_API_KEY`.
- Add explicit positive provider timeout and nonnegative retry settings; default budgets below are application limits, not guarantees of external provider charges.
- The application **must start** and existing `/health/live` and `/health/ready` must work when AI keys are missing. AI endpoints fail with a sanitized `503 provider_not_configured` until the relevant keys are configured.
- Do not log credentials, prompts, tool responses or provider error bodies by default.
- Store keys in `SecretStr`; extract via `.get_secret_value()` only at provider construction.
- `.env.example` contains empty placeholders, not real keys. Do not instantiate clients at module import time.

### FR-02 — Typed research planning

- `POST /api/v1/research/plan` accepts `{ "question": string }`.
- Request validation: strip surrounding whitespace; question length **10–2000 characters**; reject blank/invalid questions with HTTP 422.
- Planner uses a versioned system prompt and `ChatOpenAI.with_structured_output(..., method="json_schema")` (or a documented, verified provider-compatible fallback if required by the configured model).
- Response conforms to `ResearchPlan` with `objective: str`, `tasks: list[ResearchTask]`, each task containing `question: str` and `search_queries: list[str]`.
- Post-generation business validation: **1–3 tasks**, **1–3 search queries per task**, trimmed nonempty strings, query length <= 200 characters, total query count <= 9; reject invalid output rather than silently accepting it.
- Avoid embedding Pydantic `min_length`, `max_length`, or defaults in the provider-facing strict schema when unsupported; use separate post-generation validation or a provider-compatible schema.
- Return a structured error on refusal, invalid structured output or provider failure. Never return an invented plan as if the model succeeded.

### FR-03 — Tavily web search

- Implement one asynchronous search adapter backed by `AsyncTavilyClient.search`.
- Normalize each result into `SearchResult` with `title`, `url`, `snippet` and optional `published_at` (only when actually returned and parseable); no fabricated publication dates.
- Enforce input query validation, Tavily result limit (`max_results` <= 5 per call), a provider timeout, and bounded retries.
- Normalize and deduplicate source URLs for display/aggregation (remove fragments, normalize scheme/host; preserve meaningful query parameters unless deliberately specified otherwise).
- Accept only `http`/`https` source URLs and discard malformed entries; do not fetch or crawl returned URLs in Phase 2.
- Empty results are valid and produce an empty list, not an unhandled exception.
- Do not expose Tavily raw response, API keys, internal tracebacks, or provider diagnostics in public API responses.

### FR-04 — Manual tool calling and discovery

- `POST /api/v1/research/discover` accepts the same validated research question.
- Bind exactly one callable tool, `web_search(query: str)`, to the OpenAI model with `bind_tools`.
- The model may request a search; the **backend** validates arguments and executes Tavily, then returns `ToolMessage` objects with matching `tool_call_id` values.
- Handle zero, one or multiple tool calls in a model message. Set `parallel_tool_calls=False` where supported, but still enforce limits server-side rather than relying on model compliance.
- Enforce **max 3 actual search calls** and **max 4 LLM invocations** per discovery request, with a finite overall deadline and bounded provider timeouts. Stop when budgets are exhausted.
- Unknown tool names, invalid arguments, repeated/duplicate calls, empty results, malformed tool calls and provider failures must be handled predictably; no arbitrary tool execution or `eval`.
- The result includes normalized, deduplicated sources and a concise `summary` that is grounded in returned snippets; the response must clearly identify when the search was incomplete or exhausted its budget.
- No sources => do not claim the research question is answered; return a transparent `insufficient_sources`/partial outcome.
- The LLM must treat tool output as **untrusted data**, never as instructions. Prompts explicitly separate system/user instructions from web content.
- The loop must terminate on completion, budget exhaustion or failure. No recursive agent executor or LangGraph graph in Phase 2.

### FR-05 — API contracts

**`POST /api/v1/research/plan`**

Request:

```json
{ "question": "How can AI agents improve software development workflows?" }
```

Successful response (`200`):

```json
{
  "objective": "Understand AI agents in software development workflows",
  "tasks": [
    {
      "question": "Which development activities benefit from AI agents?",
      "search_queries": ["AI coding agents software development workflows"]
    }
  ]
}
```

**`POST /api/v1/research/discover`**

Request: same as above.

Successful or partially completed response (`200`):

```json
{
  "status": "completed",
  "summary": "The retrieved sources discuss agent-assisted code review and testing.",
  "sources": [
    {
      "title": "Example source",
      "url": "https://example.org/research",
      "snippet": "A retrieved snippet from Tavily.",
      "published_at": null
    }
  ],
  "search_calls": 1,
  "llm_calls": 2,
  "warnings": []
}
```

Illustrative only: these are **contract examples**, not actual research or verified sources.

`status` is `completed` or `partial`; if no sources are found, return `partial` with a warning such as `insufficient_sources`. Errors use a consistent sanitized envelope, e.g. `{ "error": { "code": "provider_unavailable", "message": "Research provider is temporarily unavailable." } }`. Use `422` for invalid requests, `503` for missing credentials or provider unavailability, `504` for deadline/timeouts; no fake successful responses. Provider-specific rate limiting may be mapped to `429` or `503`, but document one consistent policy.

### FR-06 — Testing and developer experience

- Unit tests for schemas, business validation, settings, URL normalization/deduplication, error mapping and bounded tool loop.
- Mock LLM and Tavily at boundaries; CI never makes paid calls and requires no provider credentials.
- Assert tool call IDs are matched, tool budgets cannot be bypassed, malformed args do not execute tools, duplicate URLs collapse, and provider exceptions do not leak secrets.
- Verify `/health/live` and `/health/ready` remain independent of OpenAI/Tavily.
- Add optional **explicitly opt-in** live smoke tests for configured credentials; exclude from default pytest and GitHub Actions.
- Document sample curl requests, key setup, costs/limits and common provider errors. Add `docs/learning/phase-2.md` explaining Python/LangChain concepts.

## 4. Non-functional requirements

| Area | Requirement |
|---|---|
| Security | No keys in Git, logs, errors or API responses; untrusted search snippets cannot become instructions |
| Cost | Per-request tool/LLM budgets enforced in code; token usage recorded if available, not assumed |
| Reliability | Bounded retries with backoff for transient failures only; no retries for validation/auth failures |
| Performance | Async provider calls; explicit per-call and overall timeouts; no blocking I/O in async handlers |
| Architecture | Thin routes; separate schema, provider integration, planner, tool and discovery service |
| Testability | Inject/mock model and search dependencies; no network calls at import or during unit tests |
| Observability | Safe structured logs: request ID, operation, duration, status, counts; avoid sensitive payloads |
| Portability | Keep provider-specific details behind a narrow boundary, but no generic multi-provider framework |

## 5. Explicit non-goals

- PDF parsing, embeddings, Qdrant ingestion/retrieval, RAG or citations to full document passages (Phase 3).
- LangGraph, persistent research state, planner/retriever/evaluator graph, multi-agent orchestration (Phase 4).
- Full-page extraction/crawling, arbitrary URL fetching or SSRF-sensitive browser tools.
- Next.js UI, SSE, durable jobs, research history or PostgreSQL research tables.
- Authentication, BYOK, billing, model routing, LangSmith production setup.
- Tavily Research API (it would hide the tool-calling mechanism we want to learn).

## 6. Acceptance criteria

1. Existing Phase 1 health endpoints, migrations, and CI remain green.
2. API starts with absent AI keys; calling AI endpoints returns sanitized `503` instead of crashing startup.
3. Configured OpenAI model is selected via `.env` without code changes.
4. Planner returns a validated 1–3 task `ResearchPlan` or a documented error.
5. Tavily adapter returns normalized, valid URLs with snippets, including empty-result behavior.
6. Discovery demonstrates a **real model-requested tool call**, backend execution, matching `ToolMessage`, and source aggregation.
7. Discovery cannot exceed 3 Tavily searches or 4 model invocations even if the model requests more.
8. Timeout, auth failure, rate limiting, invalid tool args, and invalid structured output have tests and safe responses.
9. Default CI and tests run without real API keys or external provider calls.
10. Optional manual live smoke test validates OpenAI + Tavily with explicit opt-in and a documented expected cost risk.
11. No LangGraph, RAG, PDF or frontend code is added prematurely.
12. README and Phase 2 learning journal explain how to run and debug the feature.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Model/schema compatibility varies | Verify selected model and LangChain `json_schema` support before coding; keep provider-facing schema simple |
| Search results contain hostile instructions | Treat all snippets as data; delimit tool messages; do not execute commands or follow embedded directions |
| Infinite/expensive tool loops | Hard cap search calls, model invocations and overall request duration |
| Provider API changes | Pin compatible versions via uv lock; use official SDKs; mock boundaries |
| Misleading research output | Only summarize actual returned snippets; partial status if evidence is insufficient |
| Optional keys break existing CI | Lazy provider construction and opt-in live tests |
| Phase 3/4 scope creep | Preserve explicit handoff contracts; no persistence, RAG or graph now |

## 8. Learning outcomes

Explain and demonstrate: Pydantic schemas vs provider-compatible JSON Schema; `ChatOpenAI`, `ainvoke`, `with_structured_output`, `bind_tools`; `AIMessage.tool_calls` and `ToolMessage`; async Tavily client; exception mapping; async dependency injection and test doubles; Python `Protocol` or callable injection when justified; tool budgets and prompt-injection boundaries. Compare with TypeScript DTOs, Zod, NestJS providers and JS promises.

## 9. Definition of done

All acceptance criteria verified, Phase 1 regression checks green, implementation reviewed for scope and security, and the team can explain the tool-calling lifecycle without relying on a framework-managed agent executor.

## 10. References

- LangChain ChatOpenAI structured output: https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI/with_structured_output
- LangChain ChatOpenAI tool calling: https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI
- Tavily Python SDK: https://github.com/tavily-ai/tavily-python
