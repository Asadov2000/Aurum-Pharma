"""Version-two receiving uses database actor, tenant and branch boundaries."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.incoming.test_unit_acceptance_v2 import (
    TABLES_V2,
    accept_v2,
    assert_no_v2_stock,
    seed_v2_incoming,
)
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context

db_session = receiving_session_fixture


async def test_cashier_cannot_initialize_or_replay_physical_receiving(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    incoming = await seed_v2_incoming(db_session)
    await db_session.commit()
    cashier = await seed_receiving_actor(
        db_engine, maintenance_engine, incoming.stock, permissions=("incoming.view", "pos.sell")
    )
    await set_receiving_context(db_session, incoming.stock, cashier)
    service = IncomingService(IncomingRepository(db_session))
    with pytest.raises(PermissionDeniedError):
        await service.accept_prepared_v2(
            incoming.document_id, actor_id=cashier, allowed_branch_ids={incoming.stock.branch_id}
        )
    await set_receiving_context(db_session, incoming.stock, runtime.actor_id)
    await assert_no_v2_stock(db_session, incoming)
    await accept_v2(db_session, incoming)
    await set_receiving_context(db_session, incoming.stock, cashier)
    with pytest.raises(PermissionDeniedError):
        await service.accept_prepared_v2(
            incoming.document_id, actor_id=cashier, allowed_branch_ids={incoming.stock.branch_id}
        )


@pytest.mark.parametrize("foreign_tenant", [False, True])
async def test_every_physical_journal_table_hides_foreign_scope_and_denies_forged_replay(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    foreign_tenant: bool,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            if foreign_tenant:
                foreign = await seed_ledger_context(support)
            else:
                branch_id = await support.scalar(
                    text(
                        "INSERT INTO branch (tenant_id,name) "
                        "VALUES (:tenant,'Foreign physical branch') RETURNING id"
                    ),
                    {"tenant": runtime.stock.tenant_id},
                )
                assert isinstance(branch_id, UUID)
                foreign = LedgerContext(
                    runtime.stock.tenant_id, branch_id, runtime.stock.catalog_id
                )
    actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    db_session.info["receiving_runtime"] = ReceivingRuntime(runtime.factory, foreign, actor)
    await set_receiving_context(db_session, foreign, actor)
    incoming = await seed_v2_incoming(db_session)
    await accept_v2(db_session, incoming)
    await db_session.commit()
    db_session.info["receiving_runtime"] = runtime
    await set_receiving_context(db_session, runtime.stock, runtime.actor_id)
    for table in TABLES_V2:
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE source_item_id=:id"),
                {"id": incoming.item_ids[0]},
            )
            == 0
        )
    expected = NotFoundError if foreign_tenant else PermissionDeniedError
    with pytest.raises(expected):
        await IncomingService(IncomingRepository(db_session)).accept_prepared_v2(
            incoming.document_id, actor_id=runtime.actor_id, allowed_branch_ids={foreign.branch_id}
        )


async def test_support_cannot_receive_or_mutate_physical_stock_even_with_forged_actor(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
) -> None:
    incoming = await seed_v2_incoming(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "SELECT set_config('app.tenant_id',:tenant,true),"
                "set_config('app.user_id',:actor,true)"
            ),
            {"tenant": str(incoming.stock.tenant_id), "actor": str(incoming.actor_id)},
        )
        assert await support.scalar(text("SELECT current_user")) == "aurum_support"
        with pytest.raises(PermissionDeniedError):
            await accept_v2(support, incoming)
        await support.rollback()
    await set_receiving_context(db_session, incoming.stock, incoming.actor_id)
    await accept_v2(db_session, incoming)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        for statement in (
            "UPDATE unit_stock_lot_v2 SET closed_packs=3 WHERE source_item_id=:id",
            "UPDATE unit_stock_lot_v2 SET purchase_cost_per_pack=11 WHERE source_item_id=:id",
            "DELETE FROM unit_stock_entry_v2 WHERE source_item_id=:id",
            "UPDATE incoming_item SET unit_packaging_snapshot_v2=NULL WHERE id=:id",
            "UPDATE incoming_item SET purchase_price=11 WHERE id=:id",
            "DELETE FROM incoming_item WHERE id=:id",
        ):
            with pytest.raises(DBAPIError):
                async with support.begin_nested():
                    await support.execute(text(statement), {"id": incoming.item_ids[0]})
        await support.rollback()


async def test_physical_receiving_is_audited_without_exposing_purchase_cost(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_v2_incoming(db_session)
    await accept_v2(db_session, incoming)
    audited = set(
        (
            await db_session.execute(
                text(
                    "SELECT DISTINCT table_name FROM audit_log "
                    "WHERE tenant_id=:tenant AND table_name=ANY(:tables)"
                ),
                {"tenant": incoming.stock.tenant_id, "tables": list(TABLES_V2)},
            )
        ).scalars()
    )
    assert audited == set(TABLES_V2)
    snapshots = (
        await db_session.execute(
            text(
                "SELECT old_values,new_values,changed_fields FROM audit_log "
                "WHERE tenant_id=:tenant AND table_name='unit_stock_lot_v2'"
            ),
            {"tenant": incoming.stock.tenant_id},
        )
    ).all()
    assert snapshots
    found_cost = False
    for record in snapshots:
        for snapshot in record:
            if snapshot is not None and "purchase_cost_per_pack" in snapshot:
                assert snapshot["purchase_cost_per_pack"] == "***"
                found_cost = True
    assert found_cost
