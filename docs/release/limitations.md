# Limitations

The current private build adds login and owner-scoped research on the single-operator MVP. It is still one host, one API process, and loopback-only. These notes are not approval to deploy it on the public internet, and they do not tag `v0.2.0`. The contract is [ADR-008](../adr/ADR-008.md). Current acceptance evidence belongs in the [Alpha checklist](alpha-checklist.md).

## Authentication and access

- Research, evidence, and history require a session. Anyone who can reach the app can register a regular account and sign in immediately. Email addresses are unverified. The operator CLI also provisions accounts. Pilot access must be restricted before the registration page. An admin can read evaluation history, diagnostics, and usage through the `GET /api/v1/admin/...` routes. Those responses are aggregates and allowlisted fields. They do not return another account's question, report, or source name, and they do not start a run. The pages do not make this build a public host, and they do not tag `v0.2.0`.
- Login allows 5 failures per 15 minutes for one normalized email and 30 for the address on the API socket. A full bucket returns 429 before the password hash runs. An email that cannot be normalized counts only against the address. The API ignores `X-Forwarded-For` and `Forwarded`. Through the web proxy the socket address is the web container, so the address cap is instance-wide. Trusting a client-supplied forwarding header would let a caller choose a fresh bucket.
- Plan, discover, run, stream, PDF upload, web ingestion, and search share 10 requests per 60 seconds per user. Minting a scope is a separate 30 per hour. Each user can hold one `/run` and one `/stream`. The process still allows at most `NEXUS_STREAM_MAX_CONCURRENT` streams (default 2, maximum 8).
- Those counters are process memory. A restart clears them. There is one API process and no TLS in this stack.
- Logout, password reset, or a disabled account ends a live stream at the next heartbeat. A provider call already in flight can finish and spend its cost. The successful history write then re-reads the locked session and stores `interrupted` with no report when that session is gone. `/run` has no history row and notices at the next provider node.
- The release stack binds Web to `127.0.0.1` only. Remote access must go through an SSH tunnel, a VPN, or an independently enforced gate ([Private remote access](deployment.md#private-remote-access)). A public deployment is not supported.

## Tenant isolation

- Every research id has one owner. History, runs, uploads, web ingestion, search, and the graph read that owner in PostgreSQL before Qdrant or a checkpoint.
- An admin session uses the same check. Another account receives 404 and does not receive the question.
- Qdrant searches stay filtered by `research_id`. Payloads do not carry an owner. The owner check is what keeps a guessed id from opening the vector store.
- Rows upgraded from the MVP are owned by a quarantine user that cannot log in, until `adopt-scope` assigns a reviewed id.

## Request-scoped execution

- A research run executes only while its HTTP request is open. There is no worker, queue, scheduler or background job.
- Closing the tab, pressing Stop, a proxy timeout, an API restart or a crash ends the run. Nothing resumes it. LangGraph checkpoints are stored but never used to continue a thread.
- A run cut off before its status write completes stays `running` ([Stale `running` rows](operations.md#stale-running-rows)). There is no operator cleanup tool; the owner can remove the row from History. The report page shows that row as the last recorded state. It does not mean the request is still running.
- Each user can have one active stream. Across users, at most `NEXUS_STREAM_MAX_CONCURRENT` (default 2, maximum 8) streams run at once per API process; more get 429.
- Standard runs allow 2 rounds, 6 searches, 20 logical model calls, 8 attempted pages, 24 passages and 180 seconds. Deep runs allow 4 rounds, 12 searches, 40 model calls, 20 attempted pages, 50 passages and 600 seconds. Hitting a cap produces `partial` or `insufficient_evidence`; see [research quality](../research-quality.md).
- Checkpoint rows are never deleted, including when a run is removed from history.

## Removing a run from history

- **Remove** in History calls `DELETE /api/v1/research/runs/{run_id}`. It deletes that owned run row and its report. Another account receives 404.
- It keeps the source rows, the indexed passages in Qdrant, the research scope, and the LangGraph checkpoints. Checkpoints still contain the question. Removal hides a run; it does not erase the data behind it.
- A run this API process is still streaming returns 409 `run_in_progress`. Stop it first. After an API restart, a stale `running` row can be removed.

## PDF processing

- Only text PDFs are read. There is no OCR; scanned pages yield no text, and a fully scanned PDF is rejected.
- Encrypted or unreadable PDFs are rejected. The default caps are 10 MiB and 100 pages.
- The original PDF bytes are not stored. A PDF citation is a `PDF · page N` label, not a link to the file. The report page does not offer the original PDF for download. Rebuilding lost PDF evidence requires uploading the file again.
- There is no keyword index and no reranker; retrieval is vector similarity only. A similarity score is not a probability that a passage is true.
- In the release stack, `NEXUS_RAG_COLLECTION` and the chunking settings are not configurable; they stay at `nexus_evidence_v1` and 800/120 tokens.

## Report grounding

- Citation ids are assigned by the server to retrieved passages, and the API rejects unknown ids and URLs outside the retrieved snapshot. A valid citation means the passage was retrieved for this run. It does not prove the sentence is entailed by the passage.
- Version 2 reports check each block against cited evidence, allow one repair, and remain partial when checking fails or coverage is incomplete. The checker is a model assessment, not a proof of entailment.
- Text copied from a retrieved page, including injected instructions, can remain in a cited section. It cannot add sources or call other tools ([failure matrix](../evaluation/failure-matrix.md)).

## Evaluation

- The CI gate is the offline suite: frozen fixture rank lists and authored citation and grounding cases, compared byte for byte with [`baseline.json`](../evaluation/baseline.json). Its Recall@5, Precision@5 and MRR@5 describe those fixtures. They are not measurements of live `text-embedding-3-small` or Qdrant retrieval quality, and not a quality threshold for production.
- The optional live judge and the live embedding test are manual, informational and not compared with the baseline. Their labels are estimates on a small authored set. Saved outputs such as `docs/evaluation/outputs/nexus-eval-live.json` are examples, not release metrics.
- An imported evaluation stores `executed_at` as null. The admin UI says the execution time is unknown. The import clock is not the time the suite ran.
- The [30-case quality benchmark](../evaluation/research-quality/README.md) has capture and blinded-review tools; no before/after acceptance result is claimed. See [`methodology.md`](../evaluation/methodology.md).

## Cost telemetry

- Cost appears in the `research.usage` log event. The admin usage response does not read that log or `UsageBuffer`. It sums logical graph counts and leaves `cost_estimate` null, `coverage` `unavailable`, and `pricing_version` null, including when `llm_calls` is positive. That is unknown, not `$0`.
- Inside a diagnostics window of at most 90 days, that usage aggregate still loads each `usage` object into the API process. `GraphUsage` rejects extra fields and non-integers; those runs are counted as missing and are not returned. The window is the Alpha bound. The scan does not remove the cap, and a positive `llm_calls` stays an unknown cost.
- Amounts are estimates from a fixed price table (pricing version `2026-10-19`: `gpt-4o-mini` $0.15 / $0.60 per 1M input / output tokens, `text-embedding-3-small` $0.02 per 1M tokens). They are not an invoice and do not follow later price changes.
- Coverage is usually `partial`: OpenAI SDK retries (`NEXUS_AI_MAX_RETRIES`, default 1) are not visible, and missing token usage is left unknown rather than counted as zero. Tavily cost is unavailable unless `NEXUS_TAVILY_USD_PER_SEARCH` is set.
- A model not in the price table is not priced.

## Production restrictions

- One host, one Compose project. No high availability, replication, horizontal scaling or zero-downtime upgrade.
- Tested on linux/amd64 only. No multi-architecture images are published; images are built locally from the repository.
- Images are built from lockfiles with digest-pinned base images. Bit-for-bit reproducible images are not claimed.
- Secrets live in `.env.release`, a plain file read by Docker Compose. Values are visible to anyone who can run `docker inspect` on the host. It is not a secret manager.
- Backups are manual cold volume copies; restoration is documented but not exercised by CI ([Backups](operations.md#backups)). Rollback to the MVP requires the paired pre-upgrade backup. Export new v2 reports before restoring.
- Container logs are not rotated by the stack. Tracing is off by default, and Jaeger keeps traces in memory only.
- No public SaaS security posture: no WAF, no data retention policy, and no account or evidence deletion. Removing a run from history is not data erasure (see above). The process-local limits above are not a substitute for one.
