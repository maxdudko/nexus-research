"""JSON logs with an allowlist. The formatter never raises into a request."""

import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TextIO

from opentelemetry import trace

from nexus.core.config import Settings
from nexus.observability.context import current_context

_HANDLER_FLAG = "_nexus_log_handler"
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_SECRET = re.compile(r"\b(?:sk|tvly)-[A-Za-z0-9_\-]{4,}\b")
_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_TOKEN = re.compile(r"^[a-z0-9_.:-]{1,64}$")
_ALLOW = frozenset(
    {
        "error_code",
        "run_id",
        "research_id",
        "stage",
        "iteration",
        "operation",
        "outcome",
        "duration_ms",
        "trace_id",
        "span_id",
        "provider",
        "model",
        "usage_source",
        "input_tokens",
        "output_tokens",
        "embedding_tokens",
        "physical_attempts",
        "logical_operations",
        "coverage",
        "pricing_version",
        "currency",
        "dropped",
        "operation_count",
        "estimated_usd",
        "unknown_operations",
        "action",
        "target_user_id",
    }
)
_FORBIDDEN = frozenset(
    {
        "prompt",
        "messages",
        "content",
        "evidence",
        "excerpt",
        "api_key",
        "authorization",
        "cookie",
        "url",
        "query",
        "headers",
        "embedding",
        "vector",
        "secret",
        "body",
        "password",
        "token",
        "pepper",
    }
)
_EVENTS = frozenset({"operation", "research.usage", "identity.audit"})
_STRING_FIELDS = frozenset(
    {
        "error_code",
        "stage",
        "operation",
        "outcome",
        "provider",
        "model",
        "usage_source",
        "coverage",
        "pricing_version",
        "currency",
        "estimated_usd",
        "trace_id",
        "span_id",
        "run_id",
        "research_id",
        "action",
        "target_user_id",
    }
)


def scrub(text: str) -> str:
    """Remove URLs and key-shaped tokens from a log message."""
    scrubbed = _URL.sub("[redacted-url]", text)
    scrubbed = _SECRET.sub("[redacted-secret]", scrubbed)
    return _BEARER.sub("[redacted-secret]", scrubbed)


def safe_token(value: object) -> str | None:
    """Keep a short identifier. Anything else is omitted."""
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        return None
    return value


class RedactionFilter(logging.Filter):
    """Drop non-allowlisted fields and strip secrets from the message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            hidden: list[str] = []
            for key in list(record.__dict__):
                if key in _FORBIDDEN:
                    hidden.extend(_strings(record.__dict__.pop(key)))
            payload = getattr(record, "nexus", None)
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if key not in _ALLOW:
                        hidden.extend(_strings(value))
                record.nexus = _allowlisted(payload)
            message = scrub(record.getMessage())
            for secret in hidden:
                if secret:
                    message = message.replace(secret, "[redacted]")
            record.msg = message
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        except Exception:
            record.msg = "log_redaction_failed"
            record.args = ()
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line. A formatting error becomes a fixed event."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            payload: dict[str, object] = {
                "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "level": record.levelname,
                "logger": record.name,
                "event": record.getMessage(),
            }
            extra = getattr(record, "nexus", None)
            if isinstance(extra, Mapping):
                for key, value in extra.items():
                    if key in _ALLOW and _safe_value(key, value):
                        payload[key] = _json_value(value)
            context = current_context()
            if context.run_id is not None and "run_id" not in payload:
                payload["run_id"] = str(context.run_id)
            if context.research_id is not None and "research_id" not in payload:
                payload["research_id"] = str(context.research_id)
            if context.stage is not None and "stage" not in payload:
                stage = safe_token(context.stage)
                if stage is not None:
                    payload["stage"] = stage
            if (
                type(context.iteration) is int
                and context.iteration >= 0
                and "iteration" not in payload
            ):
                payload["iteration"] = context.iteration
            payload.update(_trace_ids())
            return json.dumps(payload, separators=(",", ":"))
        except Exception:
            return '{"event":"log_failed","level":"ERROR"}'


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> None:
    """Attach one redacting handler to the ``nexus`` logger."""
    logger = logging.getLogger("nexus")
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_FLAG, False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream)
    setattr(handler, _HANDLER_FLAG, True)
    handler.addFilter(RedactionFilter())
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    logger.addHandler(handler)
    logger.propagate = False


def log_event(event: str, **fields: object) -> None:
    """Emit one fixed event. Unknown names and unsafe fields are dropped."""
    try:
        if event not in _EVENTS:
            return
        logging.getLogger("nexus.observability").info(
            event,
            extra={"nexus": _allowlisted(fields)},
        )
    except Exception as exc:
        logging.getLogger("nexus.observability").warning(
            "log event failed: %s",
            type(exc).__name__,
        )


def _allowlisted(fields: Mapping[str, object]) -> dict[str, object]:
    kept: dict[str, object] = {}
    for key, value in fields.items():
        if key in _ALLOW and key not in _FORBIDDEN and _safe_value(key, value):
            kept[key] = value
    return kept


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    return []


def _safe_value(key: str, value: object) -> bool:
    if value is None:
        return False
    if key in _STRING_FIELDS:
        if not isinstance(value, str) or safe_token(value) is None:
            return False
        return _SECRET.search(value) is None
    if type(value) is bool:
        return False
    if type(value) is int:
        return value >= 0
    if type(value) is float:
        return value >= 0 and value == value and value != float("inf")
    return False


def _json_value(value: object) -> object:
    if type(value) is float:
        return round(value, 3)
    return value


def _trace_ids() -> dict[str, str]:
    try:
        span = trace.get_current_span()
        context = span.get_span_context()
        if not context.is_valid:
            return {}
        return {
            "trace_id": format(context.trace_id, "032x"),
            "span_id": format(context.span_id, "016x"),
        }
    except Exception:
        return {}
