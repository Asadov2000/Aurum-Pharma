"""Observe a real PostgreSQL document lock between competing draft writers."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import ConflictError
from app.domains.incoming.draft_service import IncomingDraftService
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.schemas import IncomingDraftFields
from app.domains.incoming.service import IncomingService
from tests.domains.incoming import test_acceptance_concurrency as acceptance_fixtures
from tests.domains.incoming.test_acceptance_concurrency import (
    IncomingFixture,
    _backend_pid,
    _wait_until_blocked,
)

# Register the existing pytest fixture without importing a name shadowed by parameters.
committed_incoming = acceptance_fixtures.committed_incoming


def _fields(qty: str) -> IncomingDraftFields:
    return IncomingDraftFields(
        catalog_id="",
        batch_number="",
        manufactured_at="",
        expires_at="",
        qty=qty,
        purchase_price="",
        sale_price="",
    )


async def test_competing_writers_wait_for_document_lock_then_reject_stale_revision(
    committed_incoming: IncomingFixture,
    maintenance_engine: AsyncEngine,
) -> None:
    if not (maintenance_engine.url.database or "").endswith("_test"):
        raise RuntimeError("Draft concurrency requires a disposable database")
    factory, context = committed_incoming
    document_id, row_id = context.document_ids[0], uuid4()
    second_task: asyncio.Task[None] | None = None
    try:
        async with factory.begin() as session:
            await IncomingDraftService(IncomingService(IncomingRepository(session))).save(
                document_id,
                row_id,
                expected_revision=0,
                fields=_fields("1"),
                actor_id=uuid4(),
            )
        second_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

        async def second_writer() -> None:
            async with factory.begin() as session:
                second_pid.set_result(await _backend_pid(session))
                await IncomingDraftService(IncomingService(IncomingRepository(session))).save(
                    document_id,
                    row_id,
                    expected_revision=1,
                    fields=_fields("3"),
                    actor_id=uuid4(),
                )

        async with factory() as first:
            await first.begin()
            first_pid = await _backend_pid(first)
            await IncomingDraftService(IncomingService(IncomingRepository(first))).save(
                document_id,
                row_id,
                expected_revision=1,
                fields=_fields("2"),
                actor_id=uuid4(),
            )
            second_task = asyncio.create_task(second_writer())
            async with asyncio.timeout(10):
                blocked_pid = await second_pid
                async with factory() as observer:
                    await _wait_until_blocked(
                        observer, blocked_pid=blocked_pid, blocker_pid=first_pid
                    )
                await first.commit()
                with pytest.raises(ConflictError) as error:
                    await second_task
            assert error.value.details["current_revision"] == 2
        async with factory() as reader:
            row = await IncomingRepository(reader).get_draft_row(context.tenant_id, row_id)
            assert row is not None and row.revision == 2 and row.fields["qty"] == "2"
    finally:
        if second_task is not None and not second_task.done():
            second_task.cancel()
            await asyncio.gather(second_task, return_exceptions=True)
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE incoming_draft_row DISABLE TRIGGER trg_incoming_draft_guard")
            )
            await connection.execute(
                text("DELETE FROM incoming_draft_row WHERE tenant_id=:tenant AND id=:id"),
                {"tenant": context.tenant_id, "id": row_id},
            )
            await connection.execute(
                text("ALTER TABLE incoming_draft_row ENABLE TRIGGER trg_incoming_draft_guard")
            )
