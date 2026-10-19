"""Request-scoped correlation. Values stay out of LangGraph checkpoints."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID

from nexus.observability.usage import UsageBuffer


@dataclass(frozen=True)
class RunContext:
    """Identifiers copied into ``asyncio`` tasks with the current context."""

    run_id: UUID | None = None
    research_id: UUID | None = None
    stage: str | None = None
    iteration: int | None = None


_context: ContextVar[RunContext | None] = ContextVar(
    "nexus_run_context",
    default=None,
)
_buffer: ContextVar[UsageBuffer | None] = ContextVar(
    "nexus_usage_buffer",
    default=None,
)


def current_context() -> RunContext:
    """The correlation values visible to the current task."""
    return _context.get() or RunContext()


def current_buffer() -> UsageBuffer | None:
    """The in-process usage buffer for this task, if a run bound one."""
    return _buffer.get()


@contextmanager
def bind_run(
    *,
    run_id: UUID | None = None,
    research_id: UUID | None = None,
    stage: str | None = None,
    iteration: int | None = None,
    create_buffer: bool = False,
) -> Iterator[UsageBuffer | None]:
    """Overlay correlation fields. A nested bind keeps the current buffer."""
    parent = _context.get() or RunContext()
    child = RunContext(
        run_id=run_id if run_id is not None else parent.run_id,
        research_id=research_id if research_id is not None else parent.research_id,
        stage=stage if stage is not None else parent.stage,
        iteration=iteration if iteration is not None else parent.iteration,
    )
    context_token = _context.set(child)
    buffer_token: Token[UsageBuffer | None] | None = None
    if create_buffer and _buffer.get() is None:
        buffer_token = _buffer.set(UsageBuffer())
    try:
        yield _buffer.get()
    finally:
        # An async generator yields across this scope. Python restores a copied
        # context on the way out, and Token.reset then raises ValueError.
        # The copy is discarded with the generator, so a failed reset is safe.
        if buffer_token is not None:
            _reset(_buffer, buffer_token)
        _reset(_context, context_token)


def _reset[T](var: ContextVar[T], token: Token[T]) -> None:
    try:
        var.reset(token)
    except ValueError:
        return
