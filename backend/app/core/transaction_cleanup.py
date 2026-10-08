"""Opt-in cleanup for external resources after a known database rollback.

An unacknowledged commit may have succeeded. Its resources must be retained,
even when the request fails, so a committed job never loses its input.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import anyio
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.logging import get_logger

RollbackCleanup = Callable[[], Awaitable[None]]
_REGISTRY_KEY = "aurum_transaction_rollback_cleanup"
_REJECTED_COMMIT_STATES = frozenset({"40001", "40P01", "23503", "23505", "23514", "23P01"})
_logger = get_logger(__name__)


@dataclass
class TransactionCleanup:
    """Request-local state; listeners attach only to this session instance."""

    callbacks: list[RollbackCleanup] = field(default_factory=list)
    phase: Literal["body", "commit_attempted", "committed"] = "body"
    body_failure: BaseException | None = None

    def record_body_failure(self, exc: BaseException) -> None:
        self.body_failure = exc

    def begin_commit(self) -> None:
        if self.phase != "committed":
            self.phase = "commit_attempted"

    def mark_committed(self, session: Session) -> None:
        # SQLAlchemy emits after_commit for released SAVEPOINTs too. During
        # that event the nested transaction remains attached until close().
        if not session.in_nested_transaction():
            self.phase = "committed"

    def rollback_is_confirmed(self, exc: BaseException) -> bool:
        if self.phase == "committed":
            return False
        if self.phase == "body":
            # The transaction context propagated the same body failure: its
            # rollback completed. A different exit failure is not confirmation.
            return exc is self.body_failure
        return (
            isinstance(exc, DBAPIError)
            and not exc.connection_invalidated
            and getattr(exc.orig, "sqlstate", None) in _REJECTED_COMMIT_STATES
        )

    async def cleanup(self) -> None:
        with anyio.CancelScope(shield=True):
            for callback in reversed(self.callbacks):
                try:
                    await callback()
                except BaseException as exc:
                    # Preserve the request's original failure, never resource
                    # paths, exception messages, SQL, or request identifiers.
                    _logger.warning("transaction_cleanup_failed", error_type=type(exc).__name__)


def register_rollback_cleanup(session: AsyncSession, callback: RollbackCleanup) -> None:
    """Register only a newly owned resource whose creation has been confirmed.

    Opt-in callers must leave commit/rollback to the request dependency.
    """
    registry = session.info.get(_REGISTRY_KEY)
    if not isinstance(registry, TransactionCleanup):
        raise RuntimeError("Rollback cleanup requires a managed request transaction")
    registry.callbacks.append(callback)


@asynccontextmanager
async def managed_transaction_cleanup(session: AsyncSession) -> AsyncIterator[TransactionCleanup]:
    """Wrap ``session.begin()`` so cleanup runs after its rollback, never before."""
    if _REGISTRY_KEY in session.info:
        raise RuntimeError("Nested rollback cleanup scopes are not supported")
    registry = TransactionCleanup()
    session.info[_REGISTRY_KEY] = registry
    event.listen(session.sync_session, "after_commit", registry.mark_committed, insert=True)
    try:
        yield registry
    except BaseException as exc:
        if registry.callbacks:
            if registry.rollback_is_confirmed(exc):
                await registry.cleanup()
            else:
                _logger.warning(
                    "transaction_cleanup_retained",
                    phase=registry.phase,
                    error_type=type(exc).__name__,
                )
        raise
    finally:
        event.remove(session.sync_session, "after_commit", registry.mark_committed)
        session.info.pop(_REGISTRY_KEY, None)
