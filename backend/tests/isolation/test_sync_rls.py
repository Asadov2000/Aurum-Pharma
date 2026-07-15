"""Tenant and branch isolation for machine synchronization tables."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def support_engine_sync() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_sync() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_app_scope(
    connection: AsyncConnection,
    *,
    tenant_id: UUID,
    branch_id: UUID,
    edge_node_id: UUID | None = None,
) -> None:
    await connection.execute(
        text("SELECT set_config('app.tenant_id', :value, false)"),
        {"value": str(tenant_id)},
    )
    await connection.execute(
        text("SELECT set_config('app.branch_id', :value, false)"),
        {"value": str(branch_id)},
    )
    if edge_node_id is not None:
        await connection.execute(
            text("SELECT set_config('app.edge_node_id', :value, false)"),
            {"value": str(edge_node_id)},
        )


async def _assert_reservation_denied(
    engine: AsyncEngine,
    *,
    tenant_id: UUID,
    session_branch_id: UUID,
    target_branch_id: UUID,
    edge_node_id: UUID | None = None,
) -> None:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _set_app_scope(
                connection,
                tenant_id=tenant_id,
                branch_id=session_branch_id,
                edge_node_id=edge_node_id,
            )
            with pytest.raises(DBAPIError) as error:
                await connection.execute(
                    text(
                        "SELECT * FROM public.reserve_sync_event_position("
                        ":tenant_id, :branch_id)"
                    ),
                    {"tenant_id": tenant_id, "branch_id": target_branch_id},
                )
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def _assert_unbacked_reservation_rejected(
    engine: AsyncEngine,
    *,
    tenant_id: UUID,
    branch_id: UUID,
) -> None:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _set_app_scope(
                connection,
                tenant_id=tenant_id,
                branch_id=branch_id,
            )
            await connection.execute(
                text("SELECT * FROM public.reserve_sync_event_position(" ":tenant_id, :branch_id)"),
                {"tenant_id": tenant_id, "branch_id": branch_id},
            )
            with pytest.raises(DBAPIError) as error:
                await transaction.commit()
            assert getattr(error.value.orig, "sqlstate", None) == "23514"
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def _assert_edge_receipt_allocation_denied(
    engine: AsyncEngine,
    *,
    tenant_id: UUID,
    branch_id: UUID,
    register_id: UUID,
    edge_node_id: UUID,
) -> None:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _set_app_scope(
                connection,
                tenant_id=tenant_id,
                branch_id=branch_id,
                edge_node_id=edge_node_id,
            )
            with pytest.raises(DBAPIError) as error:
                await connection.execute(
                    text(
                        "SELECT * FROM public.allocate_register_receipt("
                        ":tenant_id, :register_id)"
                    ),
                    {"tenant_id": tenant_id, "register_id": register_id},
                )
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def test_edge_session_is_restricted_to_its_branch(
    support_engine_sync: AsyncEngine,
    app_engine_sync: AsyncEngine,
) -> None:
    tenant_id = None
    try:
        async with support_engine_sync.begin() as connection:
            tenant_id = (
                await connection.execute(
                    text(
                        "INSERT INTO tenant (name, contact_email) "
                        "VALUES (:name, :email) RETURNING id"
                    ),
                    {
                        "name": f"Sync RLS {uuid4().hex[:8]}",
                        "email": f"sync-rls-{uuid4().hex[:8]}@aurum.tj",
                    },
                )
            ).scalar_one()
            branch_rows = await connection.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) VALUES "
                    "(:tenant_id, 'A'), (:tenant_id, 'B') RETURNING id"
                ),
                {"tenant_id": tenant_id},
            )
            branch_ids = [row[0] for row in branch_rows]
            register_rows = await connection.execute(
                text(
                    "INSERT INTO register (tenant_id, branch_id, name) VALUES "
                    "(:tenant_id, :a, 'Register A'), "
                    "(:tenant_id, :b, 'Register B') RETURNING id"
                ),
                {"tenant_id": tenant_id, "a": branch_ids[0], "b": branch_ids[1]},
            )
            register_ids = [row[0] for row in register_rows]
            stream_rows = await connection.execute(
                text(
                    "SELECT branch_id, writer_node_id FROM sync_stream "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
            cloud_by_branch = {row[0]: row[1] for row in stream_rows}
            cloud_ids = [cloud_by_branch[branch_id] for branch_id in branch_ids]
            edge_rows = await connection.execute(
                text(
                    "INSERT INTO sync_node ("
                    "tenant_id, branch_id, node_kind, mode, status, display_name, "
                    "register_id, credential_kid, credential_hash, credential_issued_at, "
                    "credential_expires_at, shadow_start_origin_node_id, "
                    "shadow_start_writer_epoch"
                    ") VALUES "
                    "(:tenant_id,:a,'edge','shadow_readonly','active','Edge A',"
                    ":ra,gen_random_uuid(),repeat('a',64),now(),now()+interval '1 day',:ca,1),"
                    "(:tenant_id,:b,'edge','shadow_readonly','active','Edge B',"
                    ":rb,gen_random_uuid(),repeat('b',64),now(),now()+interval '1 day',:cb,1) "
                    "RETURNING id"
                ),
                {
                    "tenant_id": tenant_id,
                    "a": branch_ids[0],
                    "b": branch_ids[1],
                    "ra": register_ids[0],
                    "rb": register_ids[1],
                    "ca": cloud_ids[0],
                    "cb": cloud_ids[1],
                },
            )
            edge_ids = [row[0] for row in edge_rows]
            await connection.execute(
                text(
                    "UPDATE sync_stream SET "
                    "last_sequence = 1, current_checksum = repeat('b',64), "
                    "current_projection_checksum = repeat('d',64) "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
            for index in range(2):
                activation_id = uuid4()
                await connection.execute(
                    text(
                        "INSERT INTO sync_writer_activation ("
                        "activation_id, tenant_id, branch_id, writer_epoch, writer_node_id, "
                        "allowed_register_id, capability, state, root_source_checksum, "
                        "root_projection_checksum, current_source_checksum, "
                        "current_projection_checksum, previous_writer_epoch, "
                        "previous_terminal_sequence, "
                        "previous_terminal_source_checksum, "
                        "previous_terminal_projection_checksum, bootstrap_snapshot_hash, "
                        "activation_manifest_hash, receipt_baseline_seq, "
                        "prepare_request_hash, prepared_at, ready_at, aborted_at"
                        ") VALUES ("
                        ":activation_id,:tenant_id,:branch_id,2,:edge_id,:register_id,"
                        "'cash_sale_v1','aborted',repeat('e',64),repeat('f',64),"
                        "repeat('e',64),repeat('f',64),1,1,repeat('b',64),repeat('d',64),"
                        "repeat('1',64),repeat('2',64),0,repeat('4',64),now(),now(),now())"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "branch_id": branch_ids[index],
                        "activation_id": activation_id,
                        "edge_id": edge_ids[index],
                        "register_id": register_ids[index],
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO sync_writer_readiness ("
                        "activation_id, tenant_id, branch_id, edge_node_id, register_id, "
                        "writer_epoch, previous_sequence, previous_source_checksum, "
                        "previous_projection_checksum, bootstrap_snapshot_hash, "
                        "activation_manifest_hash, receipt_baseline_seq, request_hash"
                        ") VALUES ("
                        ":activation_id,:tenant_id,:branch_id,:edge_id,:register_id,2,1,"
                        "repeat('b',64),repeat('d',64),repeat('1',64),repeat('2',64),0,"
                        "repeat('3',64))"
                    ),
                    {
                        "activation_id": activation_id,
                        "tenant_id": tenant_id,
                        "branch_id": branch_ids[index],
                        "edge_id": edge_ids[index],
                        "register_id": register_ids[index],
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO register_receipt_counter ("
                        "tenant_id, branch_id, register_id, writer_epoch, last_receipt_seq"
                        ") VALUES (:tenant_id,:branch_id,:register_id,1,0)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "branch_id": branch_ids[index],
                        "register_id": register_ids[index],
                    },
                )
            event_ids = [uuid4(), uuid4()]
            sale_ids = [uuid4(), uuid4()]
            for index in range(2):
                await connection.execute(
                    text(
                        "INSERT INTO sync_outbox ("
                        "event_id, tenant_id, branch_id, origin_node_id, writer_epoch, "
                        "sequence, operation_id, aggregate_type, aggregate_id, event_type, "
                        "occurred_at, payload, payload_hash, stream_checksum, projection_hash, "
                        "projection_checksum"
                        ") VALUES ("
                        ":event_id,:tenant_id,:branch_id,:cloud_id,1,1,gen_random_uuid(),"
                        "'sale',:sale_id,'pos.sale.completed.v1',now(),'{}'::jsonb,"
                        "repeat('a',64),repeat('b',64),repeat('c',64),repeat('d',64))"
                    ),
                    {
                        "event_id": event_ids[index],
                        "tenant_id": tenant_id,
                        "branch_id": branch_ids[index],
                        "cloud_id": cloud_ids[index],
                        "sale_id": sale_ids[index],
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO sync_inbox ("
                        "event_id, tenant_id, branch_id, origin_node_id, writer_epoch, "
                        "sequence, event_type, schema_version, operation_id, aggregate_type, "
                        "aggregate_id, occurred_at, payload, payload_hash, stream_checksum, "
                        "projection_hash, projection_checksum, status, applied_at"
                        ") VALUES ("
                        ":event_id,:tenant_id,:branch_id,:cloud_id,1,1,"
                        "'pos.sale.completed.v1',1,gen_random_uuid(),'sale',:sale_id,now(),"
                        "'{}'::jsonb,repeat('a',64),repeat('b',64),repeat('c',64),"
                        "repeat('d',64),'applied',now())"
                    ),
                    {
                        "event_id": event_ids[index],
                        "tenant_id": tenant_id,
                        "branch_id": branch_ids[index],
                        "cloud_id": cloud_ids[index],
                        "sale_id": sale_ids[index],
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO sync_cursor ("
                        "tenant_id, branch_id, origin_node_id, writer_epoch, start_sequence, "
                        "start_source_checksum, start_projection_checksum, last_sequence, "
                        "source_checksum, projection_checksum, status"
                        ") VALUES ("
                        ":tenant_id,:branch_id,:cloud_id,1,0,repeat('0',64),repeat('0',64),"
                        "1,repeat('b',64),repeat('d',64),'synced')"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "branch_id": branch_ids[index],
                        "cloud_id": cloud_ids[index],
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO sync_sale_projection ("
                        "sale_id, tenant_id, branch_id, origin_node_id, writer_epoch, sequence, "
                        "source_event_id, operation_id, register_id, shift_id, cashier_user_id, "
                        "receipt_number, receipt_seq, sale_created_at, completed_at, total_amount, "
                        "items, payments, source_payload_hash, projection_hash"
                        ") VALUES ("
                        ":sale_id,:tenant_id,:branch_id,:cloud_id,1,1,:event_id,gen_random_uuid(),"
                        "gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),'R',1,now(),now(),0,"
                        "'[]'::jsonb,'[]'::jsonb,repeat('a',64),repeat('c',64))"
                    ),
                    {
                        "sale_id": sale_ids[index],
                        "tenant_id": tenant_id,
                        "branch_id": branch_ids[index],
                        "cloud_id": cloud_ids[index],
                        "event_id": event_ids[index],
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO sync_shadow_report ("
                        "report_id, tenant_id, branch_id, edge_node_id, origin_node_id, "
                        "writer_epoch, last_sequence, source_checksum, "
                        "expected_source_checksum, source_verified, projection_checksum, "
                        "expected_checksum, request_hash, status"
                        ") VALUES ("
                        "gen_random_uuid(),:tenant_id,:branch_id,:edge_id,:cloud_id,1,1,"
                        "repeat('b',64),repeat('b',64),true,repeat('d',64),repeat('d',64),"
                        "repeat('e',64),'matched')"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "branch_id": branch_ids[index],
                        "edge_id": edge_ids[index],
                        "cloud_id": cloud_ids[index],
                    },
                )

        async with app_engine_sync.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :value, false)"),
                {"value": str(tenant_id)},
            )
            await connection.execute(
                text("SELECT set_config('app.branch_id', :value, false)"),
                {"value": str(branch_ids[0])},
            )
            await connection.execute(
                text("SELECT set_config('app.edge_node_id', :value, false)"),
                {"value": str(edge_ids[0])},
            )
            for table in (
                "sync_stream",
                "sync_outbox",
                "sync_inbox",
                "sync_cursor",
                "sync_sale_projection",
                "sync_shadow_report",
                "sync_writer_activation",
                "sync_writer_epoch",
                "sync_writer_readiness",
                "register_receipt_counter",
            ):
                rows = await connection.execute(
                    text(f"SELECT branch_id FROM {table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                assert {row[0] for row in rows} == {branch_ids[0]}
            with pytest.raises(DBAPIError) as error:
                await connection.execute(text("SELECT id FROM sync_node"))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"

        await _assert_reservation_denied(
            app_engine_sync,
            tenant_id=tenant_id,
            session_branch_id=branch_ids[0],
            target_branch_id=branch_ids[1],
        )
        await _assert_reservation_denied(
            app_engine_sync,
            tenant_id=tenant_id,
            session_branch_id=branch_ids[0],
            target_branch_id=branch_ids[0],
            edge_node_id=edge_ids[0],
        )
        await _assert_unbacked_reservation_rejected(
            app_engine_sync,
            tenant_id=tenant_id,
            branch_id=branch_ids[0],
        )
        await _assert_edge_receipt_allocation_denied(
            app_engine_sync,
            tenant_id=tenant_id,
            branch_id=branch_ids[0],
            register_id=register_ids[0],
            edge_node_id=edge_ids[0],
        )

        async with support_engine_sync.connect() as connection:
            sequence = await connection.scalar(
                text(
                    "SELECT last_sequence FROM sync_stream "
                    "WHERE tenant_id = :tenant_id AND branch_id = :branch_id"
                ),
                {"tenant_id": tenant_id, "branch_id": branch_ids[0]},
            )
            assert sequence == 1
    finally:
        if tenant_id is not None:
            async with support_engine_sync.begin() as connection:
                for table in (
                    "sync_shadow_report",
                    "sync_writer_readiness",
                    "sync_writer_activation",
                    "sync_sale_projection",
                    "sync_cursor",
                    "sync_inbox",
                    "sync_outbox",
                    "register_receipt_counter",
                ):
                    await connection.execute(
                        text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                await connection.execute(
                    text(
                        "DELETE FROM sync_node WHERE tenant_id = :tenant_id "
                        "AND node_kind = 'edge'"
                    ),
                    {"tenant_id": tenant_id},
                )
                for table in (
                    "sync_stream",
                    "sync_writer_epoch",
                    "sync_node",
                    "register",
                ):
                    await connection.execute(
                        text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                await connection.execute(
                    text("DELETE FROM tenant WHERE id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
