# Phase 2 learning notes

This slice adds optional OpenAI settings and `POST /api/v1/research/plan`. It does not search the web. Tavily is installed and locked, and `NEXUS_DISCOVERY_TIMEOUT_SECONDS` is validated, but nothing calls Tavily yet. Tool calling, `ToolMessage`, and `/api/v1/research/discover` are the next slice.

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

`with_structured_output(..., method="json_schema", strict=True)` is the Python form of `zodResponseFormat` / structured parse. The provider is asked to return JSON matching the schema, and LangChain parses it into `ResearchPlan`. That is not tool calling. `ChatOpenAI` is constructed with the configured model, key, timeout, and retry count. Temperature is left unset because the current client has no temperature default and some models reject it.

### How do research errors compare with a Nest exception filter?

`ResearchError` carries a code, an HTTP status, and a fixed message. `register_exception_handlers` is an `ExceptionFilter`: it writes `{ "error": { "code", "message" } }` and does not copy the provider exception text. Timeouts are 504 `provider_timeout`. Rate limits are 429 `provider_rate_limited`. Authentication and other provider failures are 503 `provider_unavailable`. A refusal is 503 `model_refused`.

`Depends(get_planner)` is request-scoped injection without a custom container. The handler receives a `Planner`. Tests replace `get_planner` through `app.dependency_overrides`, the same role as `overrideProvider` in a Nest testing module. The replacement model only implements `ainvoke`. Default tests never open a socket to OpenAI.

The OpenAI SDK's `max_retries` is the only retry layer. The application does not add another one. `from None` on the mapped error drops the provider exception from the chain so a log of our error does not keep the prompt or the key.

## Not in this slice

Web search, URL normalization, `bind_tools`, and the manual tool loop are not implemented. The learning notes for those belong with that code.
