# Limitations

`v0.1.0` is a single-operator MVP for a workstation or a private demo host. The limits below are deliberate scope decisions (see [ADR-007](../adr/ADR-007.md)), not bugs awaiting a patch. Identity and multi-user features are planned for Alpha `v0.2.0` in the [roadmap](../roadmap.md).

## Authentication and access

- There is no login, session, API key or rate limit on any route. Anyone who can reach port 3000 can start research billed to the operator's OpenAI and Tavily accounts, upload PDFs, and read every stored question and report.
- The release stack binds Web to `127.0.0.1` only. Remote access must go through an SSH tunnel, a VPN or an independently enforced authentication gate ([Private remote access](deployment.md#private-remote-access)). HTTPS alone is not access control.
- A public, anonymous deployment is not supported.

## Tenant isolation

- `research_id` selects which evidence a retrieval may see. Qdrant searches are filtered by it on the server, and the tests check that another research's passages are not cited. It is not an access token: any client that knows or guesses a `research_id` can search that evidence.
- There is no `user_id`. `GET /api/v1/research/runs` lists every run on the instance, and any run can be opened by its `run_id`.

## Request-scoped execution

- A research run executes only while its HTTP request is open. There is no worker, queue, scheduler or background job.
- Closing the tab, pressing Stop, a proxy timeout, an API restart or a crash ends the run. Nothing resumes it. LangGraph checkpoints are stored but never used to continue a thread.
- A run cut off before its status write completes stays `running` permanently ([Stale `running` rows](operations.md#stale-running-rows)). There is no cleanup tool.
- At most `NEXUS_STREAM_MAX_CONCURRENT` (default 2, maximum 8) streams run at once per API process; more get 429.
- A run is bounded by default to 2 iterations, 6 searches, 13 logical model calls, 5 fetched pages, 12 passages and 180 seconds. Hitting a cap produces `partial` or `insufficient_evidence`, not a longer run.
- Checkpoint rows are never deleted.

## PDF processing

- Only text PDFs are read. There is no OCR; scanned pages yield no text, and a fully scanned PDF is rejected.
- Encrypted or unreadable PDFs are rejected. The default caps are 10 MiB and 100 pages.
- The original PDF bytes are not stored. A PDF citation is a `PDF · page N` label, not a link to the file. Rebuilding lost PDF evidence requires uploading the file again.
- There is no keyword index and no reranker; retrieval is vector similarity only. A similarity score is not a probability that a passage is true.
- In the release stack, `NEXUS_RAG_COLLECTION` and the chunking settings are not configurable; they stay at `nexus_evidence_v1` and 800/120 tokens.

## Report grounding

- Citation ids are assigned by the server to retrieved passages, and the API rejects unknown ids and URLs outside the retrieved snapshot. A valid citation means the passage was retrieved for this run. It does not prove the sentence is entailed by the passage.
- Text copied from a retrieved page, including injected instructions, can remain in a cited section. It cannot add sources or call other tools ([failure matrix](../evaluation/failure-matrix.md)).

## Evaluation

- The CI gate is the offline suite: frozen fixture rank lists and authored citation and grounding cases, compared byte for byte with [`baseline.json`](../evaluation/baseline.json). Its Recall@5, Precision@5 and MRR@5 describe those fixtures. They are not measurements of live `text-embedding-3-small` or Qdrant retrieval quality, and not a quality threshold for production.
- The optional live judge and the live embedding test are manual, informational and not compared with the baseline. Their labels are estimates on a small authored set. Saved outputs such as `docs/evaluation/outputs/nexus-eval-live.json` are examples, not release metrics.
- No benchmark of report accuracy, latency or cost on real questions is part of this release. See [`methodology.md`](../evaluation/methodology.md).

## Cost telemetry

- Cost appears only in the `research.usage` log event, not in the UI or the API response.
- Amounts are estimates from a fixed price table (pricing version `2026-10-19`: `gpt-4o-mini` $0.15 / $0.60 per 1M input / output tokens, `text-embedding-3-small` $0.02 per 1M tokens). They are not an invoice and do not follow later price changes.
- Coverage is usually `partial`: OpenAI SDK retries (`NEXUS_AI_MAX_RETRIES`, default 1) are not visible, and missing token usage is left unknown rather than counted as zero. Tavily cost is unavailable unless `NEXUS_TAVILY_USD_PER_SEARCH` is set.
- A model not in the price table is not priced.

## Production restrictions

- One host, one Compose project. No high availability, replication, horizontal scaling or zero-downtime upgrade.
- Tested on linux/amd64 only. No multi-architecture images are published; images are built locally from the repository.
- Images are built from lockfiles with digest-pinned base images. Bit-for-bit reproducible images are not claimed.
- Secrets live in `.env.release`, a plain file read by Docker Compose. Values are visible to anyone who can run `docker inspect` on the host. It is not a secret manager.
- Backups are manual cold volume copies; restoration is documented but not exercised by CI ([Backups](operations.md#backups)). There is no automated rollback, and `v0.1.0` has no earlier release to roll back to.
- Container logs are not rotated by the stack. Tracing is off by default, and Jaeger keeps traces in memory only.
- No public SaaS security posture: no WAF, abuse protection, audit log, data retention policy or data deletion API.
