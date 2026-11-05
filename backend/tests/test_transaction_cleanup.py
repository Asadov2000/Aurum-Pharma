"""External resources survive uncertain commits and are removed after rollback."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.transaction_cleanup import (
    managed_transaction_cleanup,
    register_rollback_cleanup,
)


class DatabaseRejection(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("synthetic database rejection")
        self.sqlstate = sqlstate


@asynccontextmanager
async def transaction_exit(events: list[str]) -> AsyncIterator[None]:
    try:
        yield
    except BaseException:
        events.append("rolled_back")
        raise
    else:
        events.append("committed")


@pytest.mark.asyncio
async def test_body_failure_cleans_only_after_rollback() -> None:
    events: list[str] = []
    failure = RuntimeError("synthetic endpoint failure")

    async def cleanup() -> None:
        events.append("cleanup")

    async with AsyncSession() as session:
        with pytest.raises(RuntimeError) as caught:
            async with managed_transaction_cleanup(session) as registry:
                async with transaction_exit(events):
                    register_rollback_cleanup(session, cleanup)
                    registry.record_body_failure(failure)
                    raise failure
        assert caught.value is failure
        assert session.info == {}
    assert events == ["rolled_back", "cleanup"]


@pytest.mark.asyncio
async def test_success_keeps_resource_and_discards_registry() -> None:
    events: list[str] = []

    async def cleanup() -> None:
        events.append("cleanup")

    async with AsyncSession() as session:
        async with managed_transaction_cleanup(session) as registry:
            async with transaction_exit(events):
                register_rollback_cleanup(session, cleanup)
                registry.begin_commit()
        assert session.info == {}
    assert events == ["committed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sqlstate", ["40001", "40P01", "23503", "23505", "23514", "23P01"])
async def test_server_rejected_commit_cleans_resource(sqlstate: str) -> None:
    events: list[str] = []
    failure = DBAPIError(None, None, DatabaseRejection(sqlstate))

    async def cleanup() -> None:
        events.append("cleanup")

    async with AsyncSession() as session:
        with pytest.raises(DBAPIError) as caught:
            async with managed_transaction_cleanup(session) as registry:
                register_rollback_cleanup(session, cleanup)
                registry.begin_commit()
                raise failure
        assert caught.value is failure
    assert events == ["cleanup"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("unacknowledged commit"),
        DBAPIError(None, None, DatabaseRejection("08006")),
        DBAPIError(None, None, DatabaseRejection("40001"), connection_invalidated=True),
        asyncio.CancelledError(),
    ],
)
async def test_uncertain_commit_retains_resource(failure: BaseException) -> None:
    events: list[str] = []

    async def cleanup() -> None:
        events.append("cleanup")

    async with AsyncSession() as session:
        with pytest.raises(type(failure)) as caught:
            async with managed_transaction_cleanup(session) as registry:
                register_rollback_cleanup(session, cleanup)
                registry.begin_commit()
                raise failure
        assert caught.value is failure
        assert session.info == {}
    assert events == []


@pytest.mark.asyncio
async def test_failed_rollback_retains_resource() -> None:
    events: list[str] = []
    failure = RuntimeError("rollback failed")

    async def cleanup() -> None:
        events.append("cleanup")

    async with AsyncSession() as session:
        with pytest.raises(RuntimeError) as caught:
            async with managed_transaction_cleanup(session) as registry:
                register_rollback_cleanup(session, cleanup)
                registry.record_body_failure(ValueError("endpoint failed"))
                raise failure
        assert caught.value is failure
    assert events == []


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_original_and_runs_other_callbacks() -> None:
    events: list[str] = []
    failure = ValueError("endpoint failed")

    async def cleanup_first() -> None:
        events.append("first")

    async def cleanup_second() -> None:
        events.append("second")
        raise RuntimeError("storage unavailable")

    async with AsyncSession() as session:
        with pytest.raises(ValueError) as caught:
            async with managed_transaction_cleanup(session) as registry:
                register_rollback_cleanup(session, cleanup_first)
                register_rollback_cleanup(session, cleanup_second)
                registry.record_body_failure(failure)
                raise failure
        assert caught.value is failure
    assert events == ["second", "first"]


@pytest.mark.asyncio
async def test_cancelled_body_cleans_after_rollback() -> None:
    events: list[str] = []
    failure = asyncio.CancelledError()

    async def cleanup() -> None:
        await asyncio.sleep(0)
        events.append("cleanup")

    async with AsyncSession() as session:
        with pytest.raises(asyncio.CancelledError) as caught:
            async with managed_transaction_cleanup(session) as registry:
                async with transaction_exit(events):
                    register_rollback_cleanup(session, cleanup)
                    registry.record_body_failure(failure)
                    raise failure
        assert caught.value is failure
    assert events == ["rolled_back", "cleanup"]


@pytest.mark.asyncio
async def test_registry_is_session_local_and_requires_managed_scope() -> None:
    async def cleanup() -> None:
        return None

    async with AsyncSession() as session, AsyncSession() as other:
        with pytest.raises(RuntimeError, match="managed request transaction"):
            register_rollback_cleanup(session, cleanup)
        async with managed_transaction_cleanup(session):
            register_rollback_cleanup(session, cleanup)
            with pytest.raises(RuntimeError, match="managed request transaction"):
                register_rollback_cleanup(other, cleanup)
        with pytest.raises(RuntimeError, match="managed request transaction"):
            register_rollback_cleanup(session, cleanup)


@pytest.mark.asyncio
async def test_nested_scope_cannot_replace_outer_registry() -> None:
    events: list[str] = []

    async def cleanup() -> None:
        events.append("cleanup")

    async with AsyncSession() as session:
        async with managed_transaction_cleanup(session) as registry:
            register_rollback_cleanup(session, cleanup)
            with pytest.raises(RuntimeError, match="Nested"):
                async with managed_transaction_cleanup(session):
                    pytest.fail("Nested scope unexpectedly accepted")
            assert len(registry.callbacks) == 1
    assert events == []


@pytest.mark.asyncio
async def test_transaction_without_callbacks_preserves_failure() -> None:
    failure = RuntimeError("ordinary request failure")
    async with AsyncSession() as session:
        with pytest.raises(RuntimeError) as caught:
            async with managed_transaction_cleanup(session):
                raise failure
        assert caught.value is failure
        assert session.info == {}


@pytest.mark.asyncio
async def test_postcommit_database_error_keeps_committed_resource() -> None:
    events: list[str] = []
    failure = DBAPIError(None, None, DatabaseRejection("23505"))

    async def cleanup() -> None:
        events.append("cleanup")

    def fail_after_commit(session: Session) -> None:
        raise failure

    async with AsyncSession() as session:
        # Register this first: the safety marker must still run before it.
        event.listen(session.sync_session, "after_commit", fail_after_commit)
        try:
            with pytest.raises(DBAPIError) as caught:
                async with managed_transaction_cleanup(session) as registry:
                    async with session.begin():
                        register_rollback_cleanup(session, cleanup)
                        registry.begin_commit()
            assert caught.value is failure
            assert registry.phase == "committed"
            assert not event.contains(session.sync_session, "after_commit", registry.mark_committed)
        finally:
            event.remove(session.sync_session, "after_commit", fail_after_commit)
    assert events == []


@pytest.mark.asyncio
async def test_savepoint_commit_does_not_prevent_outer_rollback_cleanup() -> None:
    events: list[str] = []
    failure = ValueError("outer transaction failed")

    async def cleanup() -> None:
        events.append("cleanup")

    async with AsyncSession() as session:
        with pytest.raises(ValueError) as caught:
            async with managed_transaction_cleanup(session) as registry:
                async with session.begin():
                    register_rollback_cleanup(session, cleanup)
                    async with session.begin_nested():
                        pass
                    assert registry.phase == "body"
                    registry.record_body_failure(failure)
                    raise failure
        assert caught.value is failure
    assert events == ["cleanup"]
