# Nexus Research

**An AI-powered deep research agent.**

Nexus Research is an experimental, Python-first application that investigates complex questions using web search, document retrieval, and an orchestrated AI workflow. Its goal is to produce structured research reports with traceable sources.

## Planned capabilities

- Break down research questions into actionable steps.
- Search the web and extract relevant information.
- Ingest PDF documents and retrieve supporting evidence with RAG.
- Orchestrate research with LangGraph and tool calling.
- Generate reports with source citations and visible execution progress.

## Planned stack

**Backend:** Python, FastAPI, Pydantic, SQLAlchemy, PostgreSQL  
**AI:** OpenAI, LangChain, LangGraph, Qdrant  
**Frontend:** Next.js  
**Tooling:** uv, pytest, Ruff, Docker Compose

## Status

🚧 **Planning / initial development.** Features listed above are planned, not yet implemented.

See the [development roadmap](docs/roadmap.md) for milestones and scope.

## Purpose

A hands-on AI engineering project focused on Python, agent orchestration, retrieval-augmented generation, tool calling, and production-oriented development practices.

## Development

Phase 1 currently bootstraps the Python package. The HTTP API, Docker Compose services, and database migrations are later steps.

### Prerequisites

- Python 3.14
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker and Docker Compose, used when the local PostgreSQL and Qdrant services are added

Install uv if it is not already on your `PATH`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Package bootstrap

Commands below run from `apps/api`.

```bash
cd apps/api
uv sync --frozen
uv run python -c "import nexus; print(nexus.__version__)"
```

`uv sync --frozen` installs the locked dependencies into `apps/api/.venv` and installs the `nexus` package in editable mode.

### For NestJS developers

| Python | NestJS / Node |
|---|---|
| `pyproject.toml` | `package.json` |
| `uv.lock` | the lockfile committed beside `package.json` |
| `.venv` | a local `node_modules` install |
| `src/nexus` | a compiled package whose imports resolve from the environment |

Import `nexus` as an installed package. `uv sync` puts it on the environment path, which is how application imports resolve.
