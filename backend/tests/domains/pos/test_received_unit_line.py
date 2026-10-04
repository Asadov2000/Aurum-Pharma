"""Received-line posting spends only accepted incoming stock, with live app scope."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import ConflictError, PermissionDeniedError
from app.domains.catalog.unit_sales import SaleFormat
from app.domains.inventory.unit_ledger_models import UnitStockLot
from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_ledger_service import (
    CommittedUnitStockResult,
    PreparedUnitLedgerService,
)
from app.domains.inventory.unit_ledger_service import _fingerprint as stock_fingerprint
from app.domains.inventory.unit_stock import UnitStockShortage
from app.domains.pos.unit_line_repository import UnitSaleLineRepository
from app.domains.pos.unit_line_service import (
    PreparedUnitSaleLineResult,
    PreparedUnitSaleLineService,
)
from app.domains.pos.unit_line_service import _fingerprint as line_fingerprint
from tests.domains.incoming.test_unit_acceptance import (
    IncomingContext,
    ReceivingRuntime,
    accept_prepared,
    seed_prepared_incoming,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import PACKAGING
from tests.domains.pos.test_unit_line import post

db_session = receiving_session_fixture


async def seed_received(
    session: AsyncSession,
    *,
    cost: Decimal = Decimal("10.00"),
) -> IncomingContext:
    incoming = await seed_prepared_incoming(session, cost=cost)
    await accept_prepared(session, incoming)
    return incoming


async def standalone_stock(session: AsyncSession, incoming: IncomingContext) -> UUID:
    result = await PreparedUnitLedgerService(UnitLedgerRepository(session)).prepare_lot(
        tenant_id=incoming.stock.tenant_id,
        branch_id=incoming.stock.branch_id,
        catalog_id=incoming.stock.catalog_id,
        operation_id=uuid4(),
        packaging=PACKAGING,
        closed_packs=3,
        expires_at=date.today() + timedelta(days=30),
        purchase_cost_per_pack=Decimal("50.00"),
        allowed_branch_ids={incoming.stock.branch_id},
    )
    return result.lot_id


async def post_received(
    session: AsyncSession,
    incoming: IncomingContext,
    *,
    quantity: int = 1,
    operation_id: UUID | None = None,
    actor_id: UUID | None = None,
    today: date | None = None,
    sale_format: SaleFormat = "unit",
) -> PreparedUnitSaleLineResult:
    return await PreparedUnitSaleLineService(UnitSaleLineRepository(session)).post_received_line(
        tenant_id=incoming.stock.tenant_id,
        branch_id=incoming.stock.branch_id,
        catalog_id=incoming.stock.catalog_id,
        operation_id=operation_id or uuid4(),
        actor_id=actor_id or incoming.actor_id,
        sale_format=sale_format,
        quantity=quantity,
        today=today or date.today(),
        allowed_branch_ids={incoming.stock.branch_id},
    )


async def received_dispense(
    session: AsyncSession,
    incoming: IncomingContext,
    quantity: int,
    *,
    operation_id: UUID | None = None,
    today: date | None = None,
) -> CommittedUnitStockResult:
    return await PreparedUnitLedgerService(UnitLedgerRepository(session)).dispense_received(
        tenant_id=incoming.stock.tenant_id,
        branch_id=incoming.stock.branch_id,
        catalog_id=incoming.stock.catalog_id,
        operation_id=operation_id or uuid4(),
        actor_id=incoming.actor_id,
        packaging=PACKAGING,
        sale_format="unit",
        quantity=quantity,
        today=today or date.today(),
        allowed_branch_ids={incoming.stock.branch_id},
    )


@pytest.mark.parametrize("cost", [Decimal("10.00"), Decimal("0.00")])
async def test_received_post_uses_original_accepted_cost_and_excludes_earlier_standalone_lot(
    db_session: AsyncSession,
    cost: Decimal,
) -> None:
    incoming = await seed_received(db_session, cost=cost)
    standalone = await standalone_stock(db_session, incoming)
    accepted = await db_session.scalar(
        text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
        {"id": incoming.item_ids[0]},
    )
    result = await post_received(db_session, incoming, quantity=3)
    assert result.snapshot.stock_source_kind == "received"
    assert result.snapshot.original_cost_amount == (Decimal("1.00") if cost else Decimal("0.00"))
    assert {part.lot_id for part in result.cost_slices} == {accepted}
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE id=:id"),
            {"id": standalone},
        )
        == 3
    )
    assert (
        await db_session.scalar(
            text("SELECT stock_source_kind FROM pos_unit_sale_command WHERE id=:id"),
            {"id": result.snapshot.command_id},
        )
        == "received"
    )
    assert (
        await db_session.scalar(
            text("SELECT stock_source_kind FROM unit_stock_operation WHERE id=:id"),
            {"id": result.snapshot.stock_operation_id},
        )
        == "received"
    )
    assert set(
        (
            await db_session.execute(
                text(
                    "SELECT stock_source_kind FROM unit_stock_operation "
                    "WHERE tenant_id=:tenant AND kind='initialize'"
                ),
                {"tenant": incoming.stock.tenant_id},
            )
        ).scalars()
    ) == {"prepared"}
    for table in ("sale", "sale_item", "sale_payment", "shift"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 0
        )


async def test_received_shortage_does_not_fall_back_to_sufficient_standalone_stock(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_received(db_session)
    standalone = await standalone_stock(db_session, incoming)
    for received_only in (True, False):
        with pytest.raises(UnitStockShortage):
            if received_only:
                await received_dispense(db_session, incoming, 61)
            else:
                await post_received(db_session, incoming, quantity=61)
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE id=:id"),
            {"id": standalone},
        )
        == 3
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM unit_stock_operation "
                "WHERE tenant_id=:tenant AND kind='dispense'"
            ),
            {"tenant": incoming.stock.tenant_id},
        )
        == 0
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_sale_command WHERE tenant_id=:tenant"),
            {"tenant": incoming.stock.tenant_id},
        )
        == 0
    )


async def test_existing_standalone_post_path_preserves_original_stock_selection(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_received(db_session)
    standalone = await standalone_stock(db_session, incoming)
    result = await post(db_session, incoming.stock, quantity=3)
    assert result.snapshot.stock_source_kind == "prepared"
    assert {part.lot_id for part in result.cost_slices} == {standalone}
    assert result.snapshot.original_cost_amount == Decimal("5.00")
    assert (
        await db_session.scalar(
            text("SELECT stock_source_kind FROM pos_unit_sale_command WHERE id=:id"),
            {"id": result.snapshot.command_id},
        )
        == "prepared"
    )


@pytest.mark.parametrize("received_first", [True, False])
async def test_received_and_standalone_line_command_id_cannot_change_source_policy(
    db_session: AsyncSession,
    received_first: bool,
) -> None:
    incoming = await seed_received(db_session)
    operation_id = uuid4()
    if received_first:
        original = await post_received(db_session, incoming, operation_id=operation_id)
        with pytest.raises(ConflictError):
            await post(db_session, incoming.stock, operation_id=operation_id)
        assert await post_received(db_session, incoming, operation_id=operation_id) == original
    else:
        original = await post(db_session, incoming.stock, operation_id=operation_id)
        with pytest.raises(ConflictError):
            await post_received(db_session, incoming, operation_id=operation_id)
        assert await post(db_session, incoming.stock, operation_id=operation_id) == original


async def test_received_replay_retains_original_snapshot_after_expiry_and_repricing(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_received(db_session)
    operation_id = uuid4()
    original = await post_received(db_session, incoming, operation_id=operation_id)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=99.00,unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{unit_price}','\"9.00\"') WHERE id=:id"
        ),
        {"id": incoming.stock.catalog_id},
    )
    assert (
        await post_received(
            db_session,
            incoming,
            operation_id=operation_id,
            today=date.today() + timedelta(days=365),
        )
        == original
    )
    with pytest.raises(ConflictError):
        await post_received(db_session, incoming, quantity=2, operation_id=operation_id)


async def test_received_direct_stock_replay_preserves_policy_and_rejects_legacy_reuse(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_received(db_session)
    operation_id = uuid4()
    result = await received_dispense(db_session, incoming, 1, operation_id=operation_id)
    assert (
        await received_dispense(
            db_session,
            incoming,
            1,
            operation_id=operation_id,
            today=date.today() + timedelta(days=365),
        )
        == result
    )
    with pytest.raises(ConflictError):
        await PreparedUnitLedgerService(UnitLedgerRepository(db_session)).dispense(
            tenant_id=incoming.stock.tenant_id,
            branch_id=incoming.stock.branch_id,
            catalog_id=incoming.stock.catalog_id,
            operation_id=operation_id,
            packaging=PACKAGING,
            sale_format="unit",
            quantity=1,
            today=date.today(),
            allowed_branch_ids={incoming.stock.branch_id},
        )
    assert (
        await db_session.scalar(
            text("SELECT stock_source_kind FROM unit_stock_operation WHERE id=:id"),
            {"id": operation_id},
        )
        == "received"
    )


async def test_received_pack_post_uses_server_pack_price_and_original_pack_cost(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_received(db_session)
    standalone = await standalone_stock(db_session, incoming)
    await db_session.execute(
        text("UPDATE tenant_catalog SET base_price=25.00 WHERE id=:id"),
        {"id": incoming.stock.catalog_id},
    )
    result = await post_received(db_session, incoming, sale_format="pack")
    assert result.snapshot.base_units == 30
    assert result.snapshot.gross_amount == Decimal("25.00")
    assert result.snapshot.original_cost_amount == Decimal("10.00")
    assert standalone not in {part.lot_id for part in result.cost_slices}


async def test_outer_rollback_restores_received_stock_and_removes_both_commands_and_line(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_received(db_session)
    operation_id = uuid4()
    outer = await db_session.begin_nested()
    await post_received(db_session, incoming, quantity=7, operation_id=operation_id)
    await outer.rollback()
    for table in ("pos_unit_sale_command", "pos_unit_sale_line"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 0
        )
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM unit_stock_operation "
                "WHERE tenant_id=:tenant AND kind='dispense'"
            ),
            {"tenant": incoming.stock.tenant_id},
        )
        == 0
    )
    repeated = await post_received(db_session, incoming, quantity=7, operation_id=operation_id)
    assert repeated.cost_slices[0].unit_offset == 0


async def test_pos_permission_without_incoming_permission_can_post_received_stock(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    incoming = await seed_received(db_session)
    await db_session.commit()
    cashier = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        incoming.stock,
        permissions=("pos.sell",),
    )
    await set_receiving_context(db_session, incoming.stock, cashier)
    result = await post_received(db_session, incoming, actor_id=cashier)
    assert result.snapshot.original_cost_amount == Decimal("0.33")
    assert (
        await post_received(
            db_session,
            incoming,
            actor_id=cashier,
            operation_id=result.snapshot.command_id,
        )
        == result
    )


async def test_current_actor_branch_and_pos_permission_are_rechecked_on_initial_and_replay(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    incoming = await seed_received(db_session)
    original = await post_received(db_session, incoming)
    await db_session.commit()
    viewer = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        incoming.stock,
        permissions=("incoming.view",),
    )
    for operation_id in (uuid4(), original.snapshot.command_id):
        with pytest.raises(PermissionDeniedError):
            await post_received(db_session, incoming, operation_id=operation_id, actor_id=uuid4())
        await set_receiving_context(db_session, incoming.stock, viewer)
        with pytest.raises(PermissionDeniedError):
            await post_received(db_session, incoming, operation_id=operation_id, actor_id=viewer)
        await set_receiving_context(db_session, incoming.stock, incoming.actor_id)
        with pytest.raises(PermissionDeniedError):
            await PreparedUnitSaleLineService(
                UnitSaleLineRepository(db_session)
            ).post_received_line(
                tenant_id=incoming.stock.tenant_id,
                branch_id=incoming.stock.branch_id,
                catalog_id=incoming.stock.catalog_id,
                operation_id=operation_id,
                actor_id=incoming.actor_id,
                sale_format="unit",
                quantity=1,
                today=date.today(),
                allowed_branch_ids=set(),
            )


async def test_foreign_tenant_or_branch_is_denied_before_received_replay_claim(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_received(db_session)
    original = await post_received(db_session, incoming)
    for operation_id in (uuid4(), original.snapshot.command_id):
        for tenant_id, branch_id in (
            (uuid4(), incoming.stock.branch_id),
            (incoming.stock.tenant_id, uuid4()),
        ):
            with pytest.raises(PermissionDeniedError):
                await PreparedUnitSaleLineService(
                    UnitSaleLineRepository(db_session)
                ).post_received_line(
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    catalog_id=incoming.stock.catalog_id,
                    operation_id=operation_id,
                    actor_id=incoming.actor_id,
                    sale_format="unit",
                    quantity=1,
                    today=date.today(),
                    allowed_branch_ids={branch_id},
                )


async def test_received_command_and_stock_source_policy_are_immutable(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_received(db_session)
    result = await post_received(db_session, incoming)
    for table, operation_id in (
        ("pos_unit_sale_command", result.snapshot.command_id),
        ("unit_stock_operation", result.snapshot.stock_operation_id),
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(f"UPDATE {table} SET stock_source_kind='prepared' WHERE id=:id"),
                    {"id": operation_id},
                )


class UnfilteredReceivedRepository(UnitLedgerRepository):
    """Simulate a service filtering bug; PostgreSQL must still protect every entry."""

    async def lock_lots(
        self,
        tenant_id: UUID,
        branch_id: UUID,
        catalog_id: UUID,
        *,
        received_only: bool = False,
    ) -> list[UnitStockLot]:
        return await super().lock_lots(tenant_id, branch_id, catalog_id, received_only=False)

    async def received_entries_are_valid(self, tenant_id: UUID, operation_id: UUID) -> bool:
        return True


async def test_database_rejects_received_stock_with_even_one_unaccepted_source(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_received(db_session)
    standalone = await standalone_stock(db_session, incoming)
    with pytest.raises(DBAPIError, match="unit_received_stock_source_invalid"):
        async with db_session.begin_nested():
            result = await PreparedUnitLedgerService(
                UnfilteredReceivedRepository(db_session)
            ).dispense_received(
                tenant_id=incoming.stock.tenant_id,
                branch_id=incoming.stock.branch_id,
                catalog_id=incoming.stock.catalog_id,
                operation_id=uuid4(),
                actor_id=incoming.actor_id,
                packaging=PACKAGING,
                sale_format="unit",
                quantity=91,
                today=date.today(),
                allowed_branch_ids={incoming.stock.branch_id},
            )
            assert len({part.lot_id for part in result.cost_slices}) == 2
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE id=:id"),
            {"id": standalone},
        )
        == 3
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM unit_stock_operation "
                "WHERE tenant_id=:tenant AND stock_source_kind='received'"
            ),
            {"tenant": incoming.stock.tenant_id},
        )
        == 0
    )


async def test_source_eligibility_helper_is_not_available_to_support_identity(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
) -> None:
    incoming = await seed_received(db_session)
    lot_id = await db_session.scalar(
        text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
        {"id": incoming.item_ids[0]},
    )
    await db_session.commit()
    async with db_engine.begin() as support:
        with pytest.raises(DBAPIError):
            async with support.begin_nested():
                await support.execute(
                    text(
                        "SELECT public.unit_lot_has_received_source("
                        ":tenant,:branch,:catalog,:lot)"
                    ),
                    {
                        "tenant": incoming.stock.tenant_id,
                        "branch": incoming.stock.branch_id,
                        "catalog": incoming.stock.catalog_id,
                        "lot": lot_id,
                    },
                )


def test_prepared_line_fingerprint_retains_pre_received_stage_golden_bytes() -> None:
    tenant = UUID("00000000-0000-4000-8000-000000000001")
    branch = UUID("00000000-0000-4000-8000-000000000003")
    catalog = UUID("00000000-0000-4000-8000-000000000004")
    assert line_fingerprint(tenant, branch, catalog, "unit", 3, Decimal("0.00")) == (
        "7b14128117404bdc26b59086f210f25cb798fd76cbab79d200909013e27146e8"
    )
    assert (
        stock_fingerprint(
            tenant_id=tenant,
            branch_id=branch,
            catalog_id=catalog,
            packaging=PACKAGING,
            kind="dispense",
            sale_format="unit",
            quantity=3,
        )
        == "797172bb9623fb18239ed43dc7e57661262e9aba08410a9809aacaf521ffdfb7"
    )
    assert line_fingerprint(
        tenant,
        branch,
        catalog,
        "unit",
        3,
        Decimal("0.00"),
        stock_source_kind="received",
    ) != line_fingerprint(tenant, branch, catalog, "unit", 3, Decimal("0.00"))
    assert stock_fingerprint(
        tenant_id=tenant,
        branch_id=branch,
        catalog_id=catalog,
        packaging=PACKAGING,
        kind="dispense",
        sale_format="unit",
        quantity=3,
        stock_source_kind="received",
    ) != stock_fingerprint(
        tenant_id=tenant,
        branch_id=branch,
        catalog_id=catalog,
        packaging=PACKAGING,
        kind="dispense",
        sale_format="unit",
        quantity=3,
    )


async def concurrent_received(
    factory: async_sessionmaker[AsyncSession],
    incoming: IncomingContext,
    operation_id: UUID,
) -> PreparedUnitSaleLineResult | UnitStockShortage:
    try:
        async with factory.begin() as session:
            await set_receiving_context(session, incoming.stock, incoming.actor_id)
            return await post_received(session, incoming, operation_id=operation_id)
    except UnitStockShortage as error:
        return error


async def test_simultaneous_received_retry_records_one_line_and_one_stock_charge(
    db_session: AsyncSession,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    incoming = await seed_received(db_session)
    await db_session.commit()
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(concurrent_received(runtime.factory, incoming, operation_id) for _ in range(4))
        ),
        timeout=15,
    )
    assert isinstance(results[0], PreparedUnitSaleLineResult)
    assert all(result == results[0] for result in results)
    await set_receiving_context(db_session, incoming.stock, incoming.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_sale_line WHERE tenant_id=:tenant"),
            {"tenant": incoming.stock.tenant_id},
        )
        == 1
    )


async def test_simultaneous_received_last_unit_has_one_winner_without_standalone_fallback(
    db_session: AsyncSession,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    incoming = await seed_received(db_session)
    standalone = await standalone_stock(db_session, incoming)
    first = await post_received(db_session, incoming, quantity=59)
    await db_session.commit()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(concurrent_received(runtime.factory, incoming, uuid4()) for _ in range(2))
        ),
        timeout=15,
    )
    winners = [result for result in results if isinstance(result, PreparedUnitSaleLineResult)]
    assert len(winners) == 1
    assert sum(isinstance(result, UnitStockShortage) for result in results) == 1
    assert first.snapshot.original_cost_amount + winners[
        0
    ].snapshot.original_cost_amount == Decimal("20.00")
    await set_receiving_context(db_session, incoming.stock, incoming.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE id=:id"),
            {"id": standalone},
        )
        == 3
    )
