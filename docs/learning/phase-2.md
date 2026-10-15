# Phase 2 learning notes

This slice adds optional OpenAI settings, `POST /api/v1/research/plan`, a Tavily search adapter, and `POST /api/v1/research/discover`. Discovery is a manual loop with hard caps. It is not a LangGraph agent.

The planner builds one `ChatOpenAI` when a request needs it, asks for a `ResearchPlan` with `method="json_schema"`, and checks the result again in application code. A missing API key returns HTTP 503. Liveness does not read the key.

## Questions

### What is `SecretStr`, and how is `get_settings()` different from reading `process.env`?

`SecretStr` is a string newtype. `str(settings.openai_api_key)` prints asterisks. `build_chat_model` passes that object into `ChatOpenAI`, which stores it and reads the raw key when the client is built. Application code does not copy the key into an ordinary `str`. In Nest, `ConfigService.get("OPENAI_API_KEY")` returns a plain `string`, so logging the config object prints the key.

`get_settings()` loads `Settings` once and caches it with `lru_cache`. That is the process-wide `ConfigService`. Tests do not boot that cache. They call `Settings(_env_file=None, ...)` through `make_settings`, which is constructing the config object directly. An empty `NEXUS_OPENAI_API_KEY` becomes `None`, so the process still starts. The plan route reports `provider_not_configured` when a request actually needs the key.

### Why is `await model.ainvoke(...)` required?

`async def` is an `async` function. `await` is how you consume the result. Forgetting `await` does not start the work later; the coroutine object just sits there. `ainvoke` is the async call on a LangChain runnable, the same role as `await provider.complete(messages)`.

The planner sends two messages. The system message is the versioned planner prompt (`plan-v1`). The human message is only the question. They stay separate so the question is data, not a new system instruction. That split is the same idea as keeping a Nest system prompt out of a user DTO.

### How is a Pydantic model different from the JSON Schema sent to OpenAI?

A Pydantic `BaseModel` is a Zod schema that is also the runtime class. `Field(min_length=10, max_length=2000)` on `ResearchQuestionRequest` is `z.string().trim().min(10).max(2000)`. FastAPI runs it before the handler and turns a failure into HTTP 422. A TypeScript interface would not check anything at runtime.

`ResearchPlan` is the provider schema. Its JSON Schema is only strings and arrays: no `minLength`, `maxLength`, or defaults. OpenAI's strict `json_schema` mode rejects some of those keywords, and LangChain rejects Pydantic field metadata in that mode. The length rules live in a `model_validator`, which is like Zod `.superRefine()`. The validator runs when the model is constructed, including when LangChain parses the provider response. It is not copied into the JSON Schema. The plan must have 1–3 tasks, 1–3 queries each, queries of at most 200 characters, and at most 9 queries in total. Invalid output becomes `invalid_model_output`. It is not trimmed down into a fake success.

`with_structured_output(..., method="json_schema", strict=True)` is the Python form of `zodResponseFormat` / structured parse. The provider is asked to return JSON matching the schema, and LangChain parses it into `ResearchPlan`. That is not tool calling. The configured model is `gpt-4o-mini`. It accepts this strict schema, so the planner has no fallback method. `ChatOpenAI` is constructed with the configured model, key, timeout, and retry count. Temperature is left unset because the current client has no temperature default and some models reject it.

### How do research errors compare with a Nest exception filter?

`ResearchError` carries a code, an HTTP status, and a fixed message. `register_exception_handlers` is an `ExceptionFilter`: it writes `{ "error": { "code", "message" } }` and does not copy the provider exception text. Timeouts are 504 `provider_timeout`. Rate limits are 429 `provider_rate_limited`. Authentication and other provider failures are 503 `provider_unavailable`. A refusal is 503 `model_refused`.

`Depends(get_planner)` is request-scoped injection without a custom container. The handler receives a `Planner`. Tests replace `get_planner` through `app.dependency_overrides`, the same role as `overrideProvider` in a Nest testing module. The replacement model only implements `ainvoke`. Default tests never open a socket to OpenAI.

The OpenAI SDK's `max_retries` is the only retry layer. The application does not add another one. `from None` on the mapped error drops the provider exception from the chain so a log of our error does not keep the prompt or the key.

### What does `bind_tools` do, and what is a `ToolMessage`?

`bind_tools([web_search], parallel_tool_calls=False)` gives the model a function declaration. It does not register a handler. The Python function `web_search` raises if something calls it. That is like handing an LLM a TypeScript function type and keeping the Nest provider private.

`ainvoke` then returns an `AIMessage`. `tool_calls` lists the calls the model requested, each with a `name`, `args`, and `id`. The discovery service checks the name and arguments, and only then calls `WebSearch.search`. For every id it appends one `ToolMessage` with the same `tool_call_id`. That id is a correlation id. The next `ainvoke` sees the assistant message and the tool results together.

The loop is a normal `while`. It stops when the model asks for nothing more, when the search cap (3) or model cap (4) is hit, or when `asyncio.timeout` cancels the request. `asyncio.timeout(90)` is `AbortSignal.timeout`: the current `await` raises `TimeoutError`, and the API returns 504. A single Tavily timeout is a different exception class and becomes a tool error instead of failing the whole request.

`async with AsyncTavilyClient(...)` opens and closes the HTTP client around one search, including when the search raises. Tavily's client uses the project's `httpx`. The OpenAI SDK uses `httpx2`. They stay separate.

Snippets are data. The tool message starts by saying so. A snippet that says to call another tool does not create a tool, because the only executed name is `web_search`, and only the discovery service calls it. The response sources are the adapter's normalized URLs. A summary that cites some other URL is replaced with a partial result. `completed` means the loop stopped inside the budget and the summary cites only those URLs. It does not fact-check the summary, and the snippets are not fetched pages. The `sources` list is the provenance.

### How does one Tavily search compare with `HttpService`?

`WebSearch.search` is `async def`. `await client.search(...)` suspends until the HTTP call finishes, like `await httpService.get()` inside a Nest provider. A plain `def` would block the event loop.

`async with AsyncTavilyClient(...)` is the cleanup for that one call. `__aenter__` opens the client and `__aexit__` closes it on success and on exception. A Nest provider's `onModuleDestroy` does the same job for the whole process. This client lasts for one search, and it is not built at import or in the FastAPI lifespan. If `NEXUS_TAVILY_API_KEY` is missing, the adapter raises before construction, so Tavily's keyless mode is never used.

Tavily's SDK uses the project's `httpx`. The OpenAI SDK uses `httpx2`. They stay separate. A Tavily timeout, an `httpx` timeout, or a connection error is retried up to `NEXUS_AI_MAX_RETRIES` extra times, with a short sleep between attempts. An invalid key, a bad request, and a rate limit are not retried. One Tavily timeout becomes an error tool message and the warning `search_failed`. The discovery request continues. A rate limit or an auth failure leaves the loop and becomes the HTTP 429 or 503 response.

### How do the budgets stop the loop?

The caps live in the discovery module: 3 Tavily calls, 4 model calls, 5 results per search, and `asyncio.timeout` around the whole request (90 seconds by default). The prompt can ask the model to stop, but the `while` loop is what enforces the caps. A model that keeps requesting searches cannot add a fourth Tavily call or a fifth model call.

Each model call increments `llm_calls` before `ainvoke`. Each real search increments `search_calls` before Tavily runs. Those counters are logical calls. A timeout retry inside `WebSearch.search`, or an OpenAI SDK retry inside `ainvoke`, does not add another count. A duplicate query, an unknown tool, and invalid arguments do not increment `search_calls`. An empty result list still counts, because Tavily was called. A payload that is not a result list is `search_failed`, not an empty success. Before any search in a batch, the loop checks every tool-call id. A missing id or a repeated id stops that batch with `malformed_tool_call` and does not call Tavily. When the fourth model response still asks for tools, those calls are answered and the result is `partial` with `budget_exhausted`. There is no fifth `ainvoke`.

`asyncio.timeout` cancels the coroutine when the deadline passes. That arrives as the builtin `TimeoutError` and the API returns 504. It is a different class from Tavily's timeout, which stays inside one search.
