"""RLS isolation across the POS aggregate and its synchronization outbox."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.time import utc_now
from app.domains.sync.repository import SyncOutboxRepository


@pytest_asyncio.fixture
async def support_engine_iso() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_iso() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_tenant(conn: AsyncConnection, tid: str) -> None:
    await conn.execute(text("SELECT set_config('app.tenant_id', :v, false)"), {"v": tid})


async def test_sale_invisible_across_tenants(
    support_engine_iso: AsyncEngine, app_engine_iso: AsyncEngine
) -> None:
    tenant_ids: list[str] = []
    user_ids: list[str] = []
    try:
        async with support_engine_iso.begin() as conn:
            tr = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "('IsoPos A','isopos-a@aurum.tj'),('IsoPos B','isopos-b@aurum.tj') "
                    "RETURNING id"
                )
            )
            tenant_ids = [str(r[0]) for r in tr.fetchall()]
            br = await conn.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) VALUES " "(:a,'A'),(:b,'B') RETURNING id"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1]},
            )
            branch_ids = [str(r[0]) for r in br.fetchall()]
            rr = await conn.execute(
                text(
                    "INSERT INTO register (tenant_id, branch_id, name) VALUES "
                    "(:ta,:ba,'R'),(:tb,:bb,'R') RETURNING id"
                ),
                {
                    "ta": tenant_ids[0],
                    "ba": branch_ids[0],
                    "tb": tenant_ids[1],
                    "bb": branch_ids[1],
                },
            )
            register_ids = [str(r[0]) for r in rr.fetchall()]
            ur = await conn.execute(
                text(
                    "INSERT INTO app_user (email, full_name) VALUES "
                    "('cashier-a@aurum.tj','A'),('cashier-b@aurum.tj','B') "
                    "RETURNING id"
                )
            )
            user_ids = [str(r[0]) for r in ur.fetchall()]
            sh = await conn.execute(
                text(
                    "INSERT INTO shift (tenant_id, branch_id, register_id, "
                    "opened_by_user_id) VALUES "
                    "(:ta,:ba,:ra,:ua),(:tb,:bb,:rb,:ub) RETURNING id"
                ),
                {
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                    "ba": branch_ids[0],
                    "bb": branch_ids[1],
                    "ra": register_ids[0],
                    "rb": register_ids[1],
                    "ua": user_ids[0],
                    "ub": user_ids[1],
                },
            )
            shift_ids = [str(r[0]) for r in sh.fetchall()]
            sr = await conn.execute(
                text(
                    "INSERT INTO sale (tenant_id, branch_id, register_id, "
                    "shift_id, cashier_user_id, status) VALUES "
                    "(:ta,:ba,:ra,:sa,:ua,'draft'),"
                    "(:tb,:bb,:rb,:sb,:ub,'draft') RETURNING id"
                ),
                {
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                    "ba": branch_ids[0],
                    "bb": branch_ids[1],
                    "ra": register_ids[0],
                    "rb": register_ids[1],
                    "sa": shift_ids[0],
                    "sb": shift_ids[1],
                    "ua": user_ids[0],
                    "ub": user_ids[1],
                },
            )
            sale_ids = [str(r[0]) for r in sr.fetchall()]
            await conn.execute(
                text(
                    "UPDATE sync_stream SET last_sequence = 1, "
                    "current_checksum = repeat('a',64), "
                    "current_projection_checksum = repeat('b',64) "
                    "WHERE tenant_id = ANY(:ids)"
                ),
                {"ids": tenant_ids},
            )
            await conn.execute(
                text(
                    "INSERT INTO sync_outbox ("
                    "tenant_id, branch_id, origin_node_id, writer_epoch, sequence, "
                    "operation_id, aggregate_type, aggregate_id, event_type, occurred_at, "
                    "payload, payload_hash, stream_checksum, projection_hash, "
                    "projection_checksum"
                    ") SELECT source_rows.tenant_id, source_rows.branch_id, sync_node.id, 1, 1, "
                    "gen_random_uuid(), 'sale', source_rows.sale_id, "
                    "'pos.sale.completed.v1', now(), "
                    "'{}'::jsonb, repeat('a',64), repeat('a',64), repeat('b',64), "
                    "repeat('b',64) FROM (VALUES "
                    "(CAST(:ta AS uuid), CAST(:ba AS uuid), CAST(:aa AS uuid)),"
                    "(CAST(:tb AS uuid), CAST(:bb AS uuid), CAST(:ab AS uuid))"
                    ") AS source_rows(tenant_id, branch_id, sale_id) "
                    "JOIN sync_node ON sync_node.tenant_id = source_rows.tenant_id "
                    "AND sync_node.branch_id = source_rows.branch_id "
                    "AND sync_node.node_kind = 'cloud'"
                ),
                {
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                    "ba": branch_ids[0],
                    "bb": branch_ids[1],
                    "aa": sale_ids[0],
                    "ab": sale_ids[1],
                },
            )

        app_event_id = uuid4()
        async with AsyncSession(app_engine_iso, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :v, true)"),
                    {"v": tenant_ids[0]},
                )
                repo = SyncOutboxRepository(session)
                position = await repo.reserve_position(
                    tenant_id=UUID(tenant_ids[0]),
                    branch_id=UUID(branch_ids[0]),
                )
                event = await repo.enqueue(
                    event_id=app_event_id,
                    tenant_id=UUID(tenant_ids[0]),
                    branch_id=UUID(branch_ids[0]),
                    origin_node_id=position.origin_node_id,
                    writer_epoch=position.writer_epoch,
                    sequence=position.sequence,
                    operation_id=uuid4(),
                    aggregate_type="sale",
                    aggregate_id=UUID(sale_ids[0]),
                    event_type="pos.sale.completed.v1",
                    schema_version=1,
                    occurred_at=utc_now(),
                    payload={"event_id": str(app_event_id)},
                    payload_hash="c" * 64,
                    stream_checksum="d" * 64,
                    projection_hash="e" * 64,
                    projection_checksum="f" * 64,
                )
                await repo.finalize_position(
                    stream_id=position.stream_id,
                    sequence=position.sequence,
                    stream_checksum="d" * 64,
                    projection_checksum="f" * 64,
                )
                assert event.event_id == app_event_id

        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            rows = (
                await app_conn.execute(
                    text("SELECT id FROM sale WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            assert len(rows) == 1, "Tenant A must see only its own sale"
            outbox_rows = (
                await app_conn.execute(
                    text("SELECT event_id FROM sync_outbox WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            assert {row[0] for row in outbox_rows} >= {app_event_id}
            assert len(outbox_rows) == 2, "Tenant A must see only its own outbox events"
    finally:
        if tenant_ids:
            async with support_engine_iso.begin() as conn:
                for tbl in (
                    "sync_shadow_report",
                    "sync_writer_readiness",
                    "sync_sale_projection",
                    "sync_cursor",
                    "sync_inbox",
                    "sync_outbox",
                    "register_receipt_counter",
                    "sync_stream",
                    "sync_writer_epoch",
                    "sync_node",
                    "sale",
                    "shift",
                    "register",
                    "branch",
                    "tenant_settings",
                ):
                    await conn.execute(
                        text(f"DELETE FROM {tbl} WHERE tenant_id = ANY(:ids)"),
                        {"ids": tenant_ids},
                    )
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                if user_ids:
                    await conn.execute(
                        text("DELETE FROM app_user WHERE id = ANY(:ids)"),
                        {"ids": user_ids},
                    )
