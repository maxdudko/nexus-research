"""Artifact and fixture fingerprints. The importer does not call a provider."""

import hashlib
import re
from pathlib import Path

from nexus.admin.errors import (
    BAD_REVISION,
    FILE_MISSING,
    MAX_ARTIFACT_BYTES,
    TOO_LARGE,
    EvaluationImportError,
)

FIXTURE_NAMES = (
    "citation_cases.json",
    "corpus.json",
    "grounding_cases.json",
    "queries.json",
    "retrieval_outputs.json",
)
_REVISION = re.compile(r"^[0-9a-fA-F]{40}$")


def artifact_sha256(raw: bytes) -> str:
    """Digest the artifact file exactly as it was read."""
    return hashlib.sha256(raw).hexdigest()


def dataset_sha256(directory: Path) -> str:
    """Digest the five fixture files in lexicographic filename order."""
    lines: list[str] = []
    for name in FIXTURE_NAMES:
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        lines.append(f"{name} {digest}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def read_artifact(path: Path) -> bytes:
    """Read one local artifact. Oversized files are rejected before parsing."""
    if not path.is_file():
        raise EvaluationImportError(FILE_MISSING)
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise EvaluationImportError(TOO_LARGE)
    raw = path.read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise EvaluationImportError(TOO_LARGE)
    return raw


def normalize_code_revision(value: str) -> str:
    """Accept one Git SHA. The importer does not resolve HEAD."""
    if _REVISION.fullmatch(value) is None:
        raise EvaluationImportError(BAD_REVISION)
    return value.lower()
