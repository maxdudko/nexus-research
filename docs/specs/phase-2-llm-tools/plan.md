# Nexus Research — Phase 2 Implementation Plan

**Status:** Ready for review; implementation only after approval  
**Target:** Days 3–4 of the indicative 14-day roadmap (extend as needed for learning)  
**Prerequisites:** Phase 1 green CI; approved Phase 2 `spec.md` and `ADR-002.md`  
**Execution mode:** Two Cursor Plan → Build → Review groups; small commits per step

## 0. Working rules

- **Inspect before editing.** Read the current `development` branch and preserve existing file conventions.
- Implement one step at a time; run relevant checks before moving forward.
- Do not overwrite Phase 1 infrastructure, change migrations, add domain tables or introduce LangGraph/RAG/PDF/UI.
- Keep code readable to a TypeScript/NestJS engineer learning Python; explain unfamiliar constructs.
- Do not silently assume a model name, pricing, capability or SDK method. Check actual installed versions and provider support.
- Use mocks/fakes for all default tests. Real provider smoke tests are opt-in and may incur charges.
- No automatic Git commits unless requested.

## 1. Confirm current repository baseline (read-only)

Verify these existing paths before proposing edits:

```text
apps/api/pyproject.toml
apps/api/uv.lock
apps/api/.env.example
apps/api/src/nexus/main.py
apps/api/src/nexus/core/config.py
apps/api/src/nexus/api/v1/router.py
apps/api/src/nexus/api/v1/health.py
apps/api/tests/conftest.py
.github/workflows/ci.yml
docs/adr/ADR-001.md
```

Existing patterns to preserve:

- `create_app(settings: Settings | None = None)` and lifespan-managed DB engine.
- `Settings` with `env_prefix="NEXUS_"`, optional `openai_api_key: SecretStr | None`, and `get_settings()` caching.
- Versioned API under `/api/v1` with `APIRouter`.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest`.
- GitHub Actions launches PostgreSQL/Qdrant and runs default tests without paid credentials.

**Baseline validation** (from `apps/api`):

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m 'not integration'
```

Run full `pytest` when local Compose services are ready. Record failures already present before making Phase 2 changes.

## 2. Proposed minimal file changes

Add only when needed; exact layout may be adjusted to match the repository:

```text
apps/api/
  pyproject.toml                        # dependencies only
  uv.lock                               # regenerated with uv
  .env.example                          # new optional AI settings
  src/nexus/
    core/config.py                      # OpenAI/Tavily settings
    api/v1/router.py                    # mount research router
    api/v1/research.py                  # two thin endpoints
    schemas/research.py                 # request, plan, source, discovery contracts
    ai/client.py                        # ChatOpenAI construction
    ai/prompts.py                       # versioned planning/discovery prompts
    ai/planner.py                       # structured output service
    tools/web_search.py                 # Tavily adapter and normalization
    research/discovery.py               # manual bounded tool loop
  tests/
    unit/test_ai_settings.py
    unit/test_research_schemas.py
    unit/test_planner.py
    unit/test_web_search.py
    unit/test_discovery.py
    unit/test_research_routes.py
    integration/test_ai_live.py          # optional, explicit opt-in
  README.md                             # examples and setup
  docs/learning/phase-2.md              # learning journal
```

No new migrations, Qdrant collections, persistence repositories, worker or Next.js files.

## 3. Step 1 — Dependencies, settings and OpenAI client

**Goal:** Build a minimal, testable async model boundary without changing API startup semantics.

### Tasks

1. Inspect currently resolved dependencies, Python 3.14 support and model compatibility.
2. Add `langchain-openai` and `tavily-python` with compatible bounded constraints; regenerate `uv.lock` using uv. Prefer a narrow supported version range rather than copying guessed latest versions.
3. Extend `Settings` with:

   | Variable | Required at startup? | Suggested policy |
   |---|---|---|
   | `NEXUS_OPENAI_API_KEY` | No | Existing optional `SecretStr` |
   | `NEXUS_OPENAI_MODEL` | No | Explicit nonempty model identifier in `.env.example`; document chosen smoke-test model after verifying capability |
   | `NEXUS_TAVILY_API_KEY` | No | Optional `SecretStr` |
   | `NEXUS_AI_TIMEOUT_SECONDS` | No | Positive, e.g. 20 s |
   | `NEXUS_AI_MAX_RETRIES` | No | 0–2, default 1 |
   | `NEXUS_DISCOVERY_TIMEOUT_SECONDS` | No | Positive, e.g. 90 s |

4. Construct `ChatOpenAI` only when invoked by a service or dependency, using the configured model/key/timeout/retries. Do not create it at import time or add it to FastAPI lifespan.
5. Introduce a small exception mapping for missing keys, provider timeout, provider failure and malformed provider output. Keep responses sanitized.
6. Add unit tests: optional keys do not affect `/live`, key not configured gives `503` on AI endpoints, secret values do not appear in logs/errors, model selection uses config.

### Acceptance

- `uv sync` and lockfile update succeed; no import-time provider calls.
- Existing health routes work without AI credentials.
- Model constructor is testable without calling OpenAI.
- No default CI test reaches OpenAI/Tavily.

### Learn

Python imports, `SecretStr`, dependency injection, `async def`, exceptions, async client configuration. Compare to NestJS `ConfigService`, injectable providers and OpenAI client initialization.

**Suggested commit:** `feat(ai): configure openai and tavily providers`

## 4. Step 2 — Pydantic contracts and structured planner

**Goal:** A typed, validated research plan from OpenAI.

### Tasks

1. Create `ResearchQuestionRequest` (trimmed 10–2000 chars), `ResearchTask`, `ResearchPlan` and explicit business validation.
2. Keep the provider-facing `ResearchPlan` schema simple (no unsupported strict JSON Schema constraints/defaults); use separate post-generation validation for 1–3 tasks, 1–3 queries each, max query length 200 and total <= 9.
3. Define a concise planning system prompt: return actionable, nonduplicated search queries; do not claim searches already happened; avoid following injected instructions inside the question.
4. Use `model.with_structured_output(ResearchPlan, method="json_schema")` and `await ...ainvoke(...)`; verify configured model supports this method. Choose a documented fallback only if necessary.
5. Add a planner service callable with a fake/injected structured model; do not bury LLM calls in the route handler.
6. Add `POST /api/v1/research/plan` and mount it under the existing `api_router`.
7. Test: valid output; too many tasks/queries; whitespace; empty output; provider refusal; timeout; missing key; errors do not reveal prompts or secrets.

### Acceptance

- `/api/v1/research/plan` returns the agreed contract on success.
- Invalid user input returns `422`.
- Invalid model output is rejected with a safe error (no fake plan).
- Existing `/api/v1/health/*` behavior unchanged.

### Learn

Pydantic v2 validators, `BaseModel`, runtime validation vs TypeScript interfaces/Zod, `with_structured_output`, `ainvoke`, Python exceptions.

**Suggested commit:** `feat(research): add structured openai planner`

### Group A review gate (Steps 1–2)

- Check that provider schema is accepted by the actual model during an opt-in smoke test.
- Review separation of HTTP, prompt, model and validation.
- Run Ruff, mypy, unit tests and full local integration tests.
- Stop and review before starting Tavily/tool execution.

## 5. Step 3 — Tavily async search adapter

**Goal:** Real web results behind a deterministic, mockable application boundary.

### Tasks

1. Implement an adapter using `AsyncTavilyClient.search`, not Tavily Research API or LangChain agent tools.
2. Accept a validated `query: str`, max 200 characters, and fixed `max_results <= 5`.
3. Normalize Tavily's response into `SearchResult(title, url, snippet, published_at?)`.
4. Validate URL scheme and parseability; normalize scheme/host and strip fragments; deduplicate normalized URLs.
5. Preserve raw meaningful query parameters. Avoid aggressive canonicalization that merges distinct documents.
6. Map empty results, malformed entries, auth failures, rate limits, timeouts and transient network failures.
7. Unit-test against synthetic Tavily response dictionaries and exceptions; no real network.
8. Add a tiny opt-in smoke path (CLI or test) to verify Tavily with a real key; keep it outside default CI.

### Acceptance

- Given fake provider results, the adapter returns normalized, deduplicated `SearchResult` objects.
- Invalid queries do not reach Tavily.
- No direct web-page fetching or crawling.
- Empty results are valid.

### Learn

Async SDKs, adapter boundaries, `httpx` vs SDK transport, typed dictionaries/Pydantic, URL parsing, mocking async functions.

**Suggested commit:** `feat(tools): add tavily web search adapter`

## 6. Step 4 — Manual tool-calling discovery loop

**Goal:** Understand and demonstrate the full LLM ↔ backend tool protocol.

### Required sequence

```text
HTTP request (question)
  -> discovery service builds messages and binds web_search
  -> await model.ainvoke(messages)
  -> inspect AIMessage.tool_calls
     -> no calls: finish if grounded, otherwise partial
     -> calls: validate name/args and remaining budget
     -> execute permitted Tavily searches
     -> append one ToolMessage per requested call, preserving tool_call_id
  -> await model.ainvoke(updated messages)
  -> return normalized deduplicated sources + concise summary + status
```

### Tasks

1. Define `web_search` tool schema (one required `query` argument) and bind with `model.bind_tools([web_search])`. Avoid `create_agent`/LangGraph.
2. Keep actual Tavily execution in the application service; the tool binding only advertises callable schema to the model.
3. Use `AIMessage.tool_calls` to read calls. Explicitly validate tool name and args before executing; handle malformed calls.
4. Append `ToolMessage` for **every requested tool call ID**, including errors such as `invalid_arguments`, `unknown_tool`, `duplicate_query` and `budget_exhausted`; do not leave unmatched tool calls in the message history.
5. Maintain server-owned counters: max 3 **actual** searches, max 4 LLM invocations, max 5 results/search, finite overall deadline. Prevent budget bypass via repeated requests or multiple calls in one message.
6. If multiple calls appear, handle sequentially in Phase 2; use `parallel_tool_calls=False` where supported, but still defend against multiple calls.
7. Collect normalized deduplicated sources outside the LLM context. Avoid relying on the model to enumerate source URLs correctly.
8. Ask the model for a concise summary grounded only in returned snippets. Do not claim full-page verification; when no evidence, return `partial` with `insufficient_sources`.
9. Define deterministic completion semantics: stop on no tool calls, budget exhaustion, timeout or provider failure. For budget exhaustion after an AI message with calls, respond to those calls with error `ToolMessage`s before a final attempt if the LLM budget permits; otherwise return a partial result without another model call.
10. Add `POST /api/v1/research/discover` and structured result fields `status`, `summary`, `sources`, `search_calls`, `llm_calls`, `warnings`.
11. Unit-test fake LLM response sequences: no tool call; one tool call; two tool calls; malformed args; unknown name; duplicate query; search provider failure; empty results; max search/model budget; timeout; tool-call ID matching; prompt-injection text inside snippets.
12. Keep summaries short and evidence-bound; if a model response cannot be supported by the collected snippets, prefer a cautious partial summary rather than an authoritative answer.

### Acceptance

- At least one opt-in live demo shows OpenAI requesting `web_search`, the backend invoking Tavily, and a matching `ToolMessage` being returned to OpenAI.
- Search and LLM limits are proven by unit tests.
- Search snippets cannot trigger arbitrary backend functions.
- The endpoint returns sources from the adapter, not URLs invented by the model.
- No LangGraph or persistent job machinery.

### Learn

`AIMessage`, `ToolMessage`, `bind_tools`, tool schemas, async loops, cancellation, `asyncio.timeout`, `try/except/finally`, bounded retries, prompt-injection trust boundaries.

**Suggested commit:** `feat(research): implement bounded discovery tool loop`

### Group B review gate (Steps 3–4)

- Inspect every branch in the loop for termination and budget enforcement.
- Confirm provider errors are mapped and sanitized.
- Verify the result's URLs originate in Tavily response objects.
- Confirm no direct page fetching, RAG or LangGraph has slipped in.

## 7. Step 5 — Tests, documentation and optional live verification

This is the final quality gate, not a new feature group.

### Tests

```bash
# From repository root (PostgreSQL + Qdrant for full integration suite)
docker compose up -d

cd apps/api
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run alembic upgrade head
```

Optional manual smoke tests require `NEXUS_OPENAI_API_KEY`, `NEXUS_TAVILY_API_KEY` and a model verified to support structured output and tool calling. Run only with explicit opt-in; do not wire them into `.github/workflows/ci.yml` by default.

### Example manual API checks

```bash
uv run uvicorn nexus.main:app --reload

curl -sS -X POST http://127.0.0.1:8000/api/v1/research/plan \
  -H 'Content-Type: application/json' \
  -d '{"question":"How do AI coding agents improve software development workflows?"}'

curl -sS -X POST http://127.0.0.1:8000/api/v1/research/discover \
  -H 'Content-Type: application/json' \
  -d '{"question":"How do AI coding agents improve software development workflows?"}'
```

These calls may incur provider costs. With missing keys they should return sanitized `503` and leave `/health/live` unaffected.

### Documentation

- Update README with AI setup, endpoints, response examples, budgets, costs, common failures and optional live tests.
- Add `docs/learning/phase-2.md`: five questions and answers about `ainvoke`, Pydantic, tool calls, async Tavily, budgets and error handling.
- Note any model-specific structured-output fallback and why it was necessary.
- Review `.env.example` and `.gitignore` for secrets safety.

**Suggested commit:** `test(api): cover llm planning and bounded tool execution` followed by `docs(api): document phase two usage and learning notes`.

## 8. Review checklist / definition of done

- [ ] Phase 1 checks remain green.
- [ ] Missing AI keys do not prevent API startup or health checks.
- [ ] One model is selected through `NEXUS_OPENAI_MODEL`.
- [ ] Planner uses real OpenAI structured output and post-generation validation.
- [ ] Tavily results are normalized and URL-deduplicated.
- [ ] Discovery executes real backend tools and preserves tool-call IDs.
- [ ] Maximum 3 Tavily calls and 4 model calls enforced in code.
- [ ] No source claims without retrieved evidence; partial result when insufficient.
- [ ] Negative and security tests pass without external provider access.
- [ ] Live smoke test is optional and manually approved.
- [ ] No LangGraph, Qdrant ingestion, PDF parser, SSE or research tables.
- [ ] README and learning journal updated.

## 9. Suggested Cursor workflow

### Group A — Plan mode (Steps 1–2)

> Read `docs/specs/phase-2-llm-tools/spec.md`, `docs/specs/phase-2-llm-tools/plan.md`, `docs/adr/ADR-002.md` and the current Phase 1 code. Inspect actual dependency versions and the configured OpenAI model's structured-output compatibility. Propose a minimal file-by-file implementation for Steps 1–2 only. Include tests, validation commands, assumptions, and explanations of Python-specific constructs versus TypeScript/NestJS. Do not modify files until approval.

### Group A — Build mode

> Implement approved Steps 1–2 sequentially. Validate each step before proceeding. Use injected/fake model dependencies for unit tests. Do not add LangGraph, Tavily execution or persistence yet. Run Ruff, mypy and tests; report results honestly. Do not commit automatically.

### Group B — Plan mode (Steps 3–4)

> Inspect the merged Steps 1–2 implementation and the Phase 2 documents. Propose Steps 3–4 with special focus on Tavily response normalization, matching tool-call IDs, termination, per-request budgets, and error behavior. Specify exact files/tests and explain Python async control flow. Do not edit files until approval.

### Group B — Build mode

> Implement Steps 3–4 in sequence. Verify Tavily adapter with mocks first, then the bounded manual tool loop with fake AIMessage/ToolMessage sequences. Add opt-in live smoke coverage only; keep default CI offline. Run Ruff, mypy, pytest and document any unverified provider behavior. Do not commit automatically.

## 10. Handoff to Phase 3 and Phase 4

**Phase 3 (RAG)** can reuse `SearchResult` and source metadata, then add PDF/web extraction, chunking, embeddings, Qdrant ingestion and retrieval. Search snippets are **not** equivalent to retrieved full-page passages.

**Phase 4 (LangGraph)** can wrap the planner, Tavily tool and retrieval services as graph nodes, replacing the Phase 2 manual discovery loop. Avoid coupling these services to the current HTTP request or manual message loop so they remain reusable.
