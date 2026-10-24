"""Import one evaluation artifact. This command does not call a model."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO
from uuid import UUID, uuid4

from nexus.admin.errors import (
    BASELINE_MISMATCH,
    METADATA_CONFLICT,
    STORE_UNAVAILABLE,
    EvaluationImportError,
)
from nexus.admin.evaluation_store import EvaluationConflict, insert_evaluation
from nexus.admin.normalization import RevisionSource, prepare_import
from nexus.admin.provenance import (
    artifact_sha256,
    normalize_code_revision,
    read_artifact,
)
from nexus.admin.schemas import EvaluationRunInsert
from nexus.core.config import Settings, get_settings
from nexus.infrastructure.database import create_db_engine, session_factory


def main(argv: list[str] | None = None) -> None:
    """Exit 0 after an import or an idempotent reimport."""
    raise SystemExit(run_cli(argv))


def run_cli(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    settings: Settings | None = None,
) -> int:
    """Validate a local artifact and insert it. Failures use a fixed sentence."""
    err = sys.stderr if stderr is None else stderr
    out = sys.stdout if stdout is None else stdout
    args = _parser().parse_args(argv)
    try:
        payload = _prepare(args)
    except EvaluationImportError as exc:
        print(exc.message, file=err)
        return 1
    resolved = settings if settings is not None else get_settings()
    try:
        stored = asyncio.run(_save(resolved, payload))
    except EvaluationConflict:
        print(METADATA_CONFLICT, file=err)
        return 1
    except Exception:
        print(STORE_UNAVAILABLE, file=err)
        return 1
    print(stored, file=out)
    return 0


def committed_baseline_path() -> Path:
    """The repository baseline this checkout can verify byte for byte."""
    return Path(__file__).resolve().parents[5] / "docs" / "evaluation" / "baseline.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nexus.admin.evaluation_import",
        description=(
            "Import one offline or live evaluation artifact into PostgreSQL. "
            "The command reads local files, checks them against the fixture set, "
            "and does not call a model."
        ),
    )
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, default=None)
    parser.add_argument("--code-revision", default=None)
    parser.add_argument("--committed-baseline", action="store_true")
    return parser


def _prepare(args: argparse.Namespace) -> EvaluationRunInsert:
    revision: str | None = None
    source: RevisionSource | None = None
    if args.code_revision is not None:
        revision = normalize_code_revision(args.code_revision)
        source = "operator_asserted"
    raw = read_artifact(args.artifact)
    baseline = _baseline_bytes() if args.committed_baseline else None
    return prepare_import(
        raw,
        fixtures=args.fixtures,
        artifact_sha256=artifact_sha256(raw),
        code_revision=revision,
        code_revision_source=source,
        committed_baseline=args.committed_baseline,
        baseline_bytes=baseline,
        imported_at=datetime.now(UTC),
        run_id=uuid4(),
    )


def _baseline_bytes() -> bytes:
    path = committed_baseline_path()
    if not path.is_file():
        raise EvaluationImportError(BASELINE_MISMATCH)
    return path.read_bytes()


async def _save(settings: Settings, payload: EvaluationRunInsert) -> UUID:
    engine = create_db_engine(str(settings.database_url))
    try:
        return await insert_evaluation(session_factory(engine), payload)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
