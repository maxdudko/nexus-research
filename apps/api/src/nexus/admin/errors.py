"""Fixed import failures. Messages do not include file contents or database URLs."""


class EvaluationImportError(Exception):
    """The artifact was rejected before a write."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


FILE_MISSING = "The artifact file was not found."
TOO_LARGE = "The artifact exceeds the import limit."
ARTIFACT_INVALID = "The artifact is not a supported evaluation report."
VERSION_UNSUPPORTED = "The report version is not supported."
FIXTURES_INVALID = "The fixture set could not be validated."
OFFLINE_MISMATCH = "The offline artifact does not match the recomputed fixtures."
LIVE_MISMATCH = "The live artifact does not match the recomputed counts."
BASELINE_OFFLINE = "A committed baseline must be an offline artifact."
BASELINE_MISMATCH = "The artifact does not match the committed baseline."
BAD_REVISION = "The code revision is not a Git SHA."
METADATA_CONFLICT = "The artifact is already stored with different metadata."
STORE_UNAVAILABLE = "The evaluation store is unavailable."
MAX_ARTIFACT_BYTES = 2_097_152
