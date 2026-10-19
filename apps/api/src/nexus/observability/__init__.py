"""Process observability. Importing this package does not export spans."""

from nexus.core.config import Settings
from nexus.observability.costs import configure_cost_settings
from nexus.observability.logging import configure_logging
from nexus.observability.tracing import configure_tracing, shutdown_tracing


def configure_observability(settings: Settings) -> None:
    """Configure logs and, when enabled, a tracer. Failures stay local."""
    try:
        configure_logging(settings)
    except Exception:
        return
    try:
        configure_cost_settings(settings)
    except Exception:
        return
    try:
        configure_tracing(settings)
    except Exception:
        return


def shutdown_observability() -> None:
    """Flush a configured exporter. A disabled process returns immediately."""
    try:
        shutdown_tracing()
    except Exception:
        return
