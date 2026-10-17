# Phase 4 learning notes

`POST /api/v1/research/run` waits for one LangGraph thread and returns a cited report. The thread is checkpointed in PostgreSQL. Nothing in this phase queues work, streams progress, or starts the thread again after the request ends.

## Questions

### Why are `research_id` and `thread_id` different?

`research_id` is the evidence scope. Passages indexed for that id stay searchable by a later run. `run_id` is the LangGraph `thread_id`. The server mints a new one for every request, including a request that repeats a `research_id`. The client cannot send either a `run_id` or a `thread_id`.

Two runs can therefore share sources without sharing a checkpoint. A checkpoint row survives closing the pool and opening a new saver. That is persistence. LangGraph resumes execution only when `ainvoke` is called again with the same `thread_id`. This API never does that, so a leftover `running` checkpoint is abandoned state, not a job.

### Why is a checkpoint not a queue or a transaction?

A checkpoint is the graph state after a node. A later process can read it. It does not enqueue a worker, and it does not roll the node back. The HTTP handler is the only thing that calls the graph. When that handler returns, crashes, or is cancelled, no other process picks the row up.

The tables (`checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) are created by `uv run python -m nexus.research.checkpoint`. Alembic does not own them. They are not a history API: the run response does not list rows, and nothing deletes old threads.

`setup()` is safe to run again. A request does not call it. The application opens a connection pool on the first run and closes that pool when the process stops. Unit tests that never call `/run` do not open it.

### What happens when the client disconnects?

The handler re-raises `CancelledError` immediately. It does not shield a follow-up task, so cancellation does not leave the graph running in the background.

Three stops write different state:

- The graph's own deadline can finish as `partial` or `insufficient_evidence` and return 200.
- The HTTP timer sits 5 seconds past `NEXUS_GRAPH_DEADLINE_SECONDS`. If that hard wait fires, the handler writes `interrupted` while the request is alive and returns 504 `deadline_exceeded` with no report. A proxy read timeout has to be longer than that outer wait.
- A client disconnect or a process death does not write a new status. The last completed node stays `running`. Nothing resumes it.

### What is repeated when a run is started again?

External calls are at least once. A new `run_id` calls Tavily and OpenAI again, even when `research_id` is reused. A node that is cancelled after the provider has responded can leave that call out of the checkpoint. Usage counters record the logical call, not each SDK retry inside it.

Web ingestion goes through the Phase 3 service. The same page is fetched again, and an unchanged body stays one source. PDF bytes are not stored, so a lost PDF index is rebuilt by uploading the file again.

### What can Phase 5 add without rewriting the nodes?

The nodes already take services from the runtime and return state updates. A later phase can stream those updates with server-sent events, or store a history the UI can list, by wrapping the same graph. It does not need a new planner, retriever, or citation check. Authorization, durable PDF storage, and automatic resume are still absent.

## Limitations

- There is no auto-resume after a cancel, a timeout, or a crash.
- There is no worker, no server-sent events, and no authentication.
- PDF bytes are not stored. `/run` does not accept a file. Index the PDF first and pass its `research_id`.
- A citation means the evidence id was retrieved for this run. It does not mean the sentence is entailed by the passage.
- Checkpoint rows are not cleaned up.
