"""Database guards keep saved reconciliation documents isolated and immutable."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.inventory.unit_legacy_reconciliation_record_repository import (
    LegacyReconciliationRecordRepository,
)
from app.domains.inventory.unit_legacy_reconciliation_record_service import (
    LegacyReconciliationRecordService,
)
from app.domains.inventory.unit_legacy_reconciliation_schemas import (
    LegacyReconciliationRecordResponse,
    LegacyReconciliationSaveRequest,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_physical_inventory_api import inventory_actor
from tests.domains.inventory.test_unit_legacy_reconciliation_api import legacy_batch
from tests.domains.inventory.test_unit_reconciliation_records_api import path, request_body
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
TABLE = "unit_legacy_reconciliation_record"


async def saved_record(client: AsyncClient, session: AsyncSession) -> UUID:
    batch = await legacy_batch(session)
    request = request_body(session)
    async with inventory_actor(session):
        response = await client.post(path(batch.id), json=request)
    assert response.status_code == 200, response.text
    return UUID(response.json()["id"])


async def count_record(session: AsyncSession, record_id: UUID) -> int:
    return int(
        await session.scalar(text(f"SELECT count(*) FROM {TABLE} WHERE id=:id"), {"id": record_id})
        or 0
    )


@pytest.mark.parametrize(
    "action", [f"UPDATE {TABLE} SET legacy_quantity=0", f"DELETE FROM {TABLE}"]
)
async def test_app_cannot_rewrite_or_delete_saved_document(
    client: AsyncClient, db_session: AsyncSession, action: str
) -> None:
    record_id = await saved_record(client, db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(action + " WHERE id=:id"), {"id": record_id})
    assert await count_record(db_session, record_id) == 1


async def test_rls_hides_record_under_another_tenant_context(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    record_id = await saved_record(client, db_session)
    context = runtime(db_session)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert await count_record(db_session, record_id) == 0
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await count_record(db_session, record_id) == 1


async def test_actual_view_only_actor_can_read_but_cannot_clone_document(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    viewer = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("batches.view",)
    )
    record_id = await saved_record(client, db_session)
    await set_receiving_context(db_session, context.stock, viewer)
    assert await count_record(db_session, record_id) == 1
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO {TABLE} "
                    "(tenant_id,id,branch_id,batch_id,catalog_id,legacy_quantity,count,created_by) "
                    "SELECT tenant_id,:new_id,branch_id,batch_id,catalog_id,legacy_quantity,"
                    f"count,:actor FROM {TABLE} WHERE id=:id"
                ),
                {"new_id": uuid4(), "actor": viewer, "id": record_id},
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)


async def test_raw_insert_cannot_hide_extra_client_fields_inside_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    record_id = await saved_record(client, db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO {TABLE} "
                    "(tenant_id,id,branch_id,batch_id,catalog_id,legacy_quantity,count,created_by) "
                    "SELECT tenant_id,:new_id,branch_id,batch_id,catalog_id,legacy_quantity,"
                    "count||jsonb_build_object('approved',true),created_by "
                    f"FROM {TABLE} WHERE id=:id"
                ),
                {"new_id": uuid4(), "id": record_id},
            )
    assert await count_record(db_session, record_id) == 1


async def save_request(
    session: AsyncSession, batch_id: UUID, request: LegacyReconciliationSaveRequest
) -> LegacyReconciliationRecordResponse:
    context = runtime(session)
    return await LegacyReconciliationRecordService(
        LegacyReconciliationRecordRepository(session)
    ).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        batch_id=batch_id,
        request=request,
        view_branch_ids={context.stock.branch_id},
        update_branch_ids={context.stock.branch_id},
    )


async def wait_for_advisory_waiter(session: AsyncSession, pid: int) -> None:
    while not await session.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND "
            "locktype='advisory' AND NOT granted)"
        ),
        {"pid": pid},
    ):
        await asyncio.sleep(0.02)


async def test_concurrent_same_uuid_waits_then_replays_one_committed_document(
    db_session: AsyncSession,
) -> None:
    batch = await legacy_batch(db_session)
    request = LegacyReconciliationSaveRequest.model_validate(request_body(db_session))
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    first = await save_request(db_session, batch.id, request)
    async with context.factory() as second:
        second.info["receiving_runtime"] = context
        await set_receiving_context(second, context.stock, context.actor_id)
        pid = int(await second.scalar(text("SELECT pg_backend_pid()")) or 0)
        task = asyncio.create_task(save_request(second, batch.id, request))
        try:
            await asyncio.wait_for(wait_for_advisory_waiter(db_session, pid), timeout=10)
            assert not task.done()
            await db_session.commit()
            repeated = await asyncio.wait_for(task, timeout=10)
            await second.commit()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert repeated == first
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await count_record(db_session, request.reconciliation_id) == 1


def migration() -> ModuleType:
    versions = Path(__file__).resolve().parents[3] / "alembic/versions"
    spec = importlib.util.spec_from_file_location(
        "unit_reconciliation_record_migration",
        versions / "0200_unit_legacy_reconciliation_record.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_downgrade_refuses_to_erase_a_saved_observation(
    client: AsyncClient, db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    record_id = await saved_record(client, db_session)
    await db_session.commit()

    def downgrade(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError) as error:
                await connection.run_sync(downgrade)
            assert getattr(error.value.orig, "sqlstate", None) == "55000"
        finally:
            await transaction.rollback()
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await count_record(db_session, record_id) == 1


@pytest.mark.parametrize("duplicate_pack", [False, True])
async def test_raw_insert_rejects_invalid_opened_pack_structure(
    client: AsyncClient, db_session: AsyncSession, duplicate_pack: bool
) -> None:
    record_id = await saved_record(client, db_session)
    pack = {
        "id": str(uuid4()),
        "opened_at": "2026-10-15T12:00:00Z",
        "intact_blisters": [3, 1],
        "opened_blisters": [{"number": 2, "remaining_units": 4 if duplicate_pack else 10}],
        "loose_units": 0,
    }
    count = {
        "packaging": {
            "version": 2,
            "unit": "tablet",
            "units_per_pack": 30,
            "units_per_blister": 10,
            "dispense_step": 1,
        },
        "closed_packs": 0,
        "opened_packs": [pack, pack] if duplicate_pack else [pack],
    }
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO {TABLE} "
                    "(tenant_id,id,branch_id,batch_id,catalog_id,legacy_quantity,count,created_by) "
                    "SELECT tenant_id,:new_id,branch_id,batch_id,catalog_id,legacy_quantity,"
                    f"CAST(:count AS jsonb),created_by FROM {TABLE} WHERE id=:id"
                ),
                {"new_id": uuid4(), "id": record_id, "count": json.dumps(count)},
            )
    assert getattr(error.value.orig, "sqlstate", None) == "23514"
    assert await count_record(db_session, record_id) == 1
