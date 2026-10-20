# Changelog

All notable changes to Nexus Research are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - Unreleased

Release candidate for the first MVP. This entry gets a date when the maintainer tags `v0.1.0` after Gate C in [`docs/release/checklist.md`](docs/release/checklist.md).

### Added

- **Foundation.** FastAPI service under `/api/v1`, validated `NEXUS_*` settings, async SQLAlchemy and Alembic, liveness and readiness (PostgreSQL and Qdrant) endpoints, and pytest, Ruff and mypy.
- **LLM and tools.** `POST /api/v1/research/plan` with strict structured output, and `POST /api/v1/research/discover` with bounded Tavily search through tool calls, fixed error codes and logical call counts.
- **Evidence (RAG).** Text-PDF and public-web-page ingestion with byte, page and chunk caps, SSRF protection, embeddings with `text-embedding-3-small`, Qdrant storage filtered by `research_id`, and `POST /api/v1/evidence/search`.
- **Research workflow.** A bounded LangGraph run (planning, discovery, ingestion, retrieval, evaluation, synthesis) with PostgreSQL checkpoints, `POST /api/v1/research/run`, the SSE endpoint `POST /api/v1/research/stream` with heartbeats and cancellation, and run history at `GET /api/v1/research/runs` and `GET /api/v1/research/runs/{run_id}`.
- **Web UI.** A Next.js workspace with question input, optional PDF upload, live stage progress, stop, saved reports, history, `https` citations and `PDF · page N` labels, and light and dark themes. The server-side `/api/v1` proxy streams SSE without buffering and keeps provider keys off the browser.
- **Reliability.** Allowlisted JSON logs with redaction, optional OpenTelemetry tracing and Jaeger, a per-run `research.usage` cost estimate with explicit coverage, a failure matrix, and an offline evaluation suite (dataset `2026-10-19.2`) compared with a committed baseline in CI, plus an opt-in live judge.
- **Release packaging.**
  - Non-root API (Python 3.14) and Web (Node 24, Next.js standalone) images built from `uv.lock` and `package-lock.json` with digest-pinned base images.
  - `compose.release.yml`: a private stack with Web on `127.0.0.1:3000`, an internal data network, named volumes, and a one-shot `init` service for Alembic migrations and checkpoint tables.
  - `.env.release.example`, and a production check that refuses the development database password.
  - `scripts/check_release_compose.py` and `scripts/release-smoke.sh`, with no paid provider calls; CI builds both images and runs the packaged smoke test.
- **Documentation.** Release [deployment](docs/release/deployment.md), [operations](docs/release/operations.md), [demo](docs/release/demo.md), [limitations](docs/release/limitations.md) and [checklist](docs/release/checklist.md) guides; [development guide](docs/development.md); a release-focused README.

### Known limitations

No authentication or per-user isolation; do not expose the stack publicly. Research is request-scoped, with no background execution or resume, and a run can be left `running`. PDF originals are not stored, there is no OCR, and PDF citations are page labels. Offline evaluation scores frozen fixtures, not live retrieval quality. Cost figures are partial estimates in logs only. Single host, linux/amd64 only. Full list: [`docs/release/limitations.md`](docs/release/limitations.md).

[Unreleased]: https://github.com/maxdudko/nexus-research/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/maxdudko/nexus-research/releases/tag/v0.1.0
