"""Unit stock reports reconstruct immutable balances and conceal unauthorized costs."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, Inexact, localcontext
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_ledger_service import PreparedLotResult, PreparedUnitLedgerService
from app.domains.inventory.unit_report_contracts import UnitStockReport
from app.domains.inventory.unit_report_repository import UnitStockReportRepository
from app.domains.inventory.unit_report_service import UnitStockReportService
from app.domains.inventory.unit_stock import UnitPackaging
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_received_unit_line import seed_received

db_session = receiving_session_fixture


def runtime(session: AsyncSession) -> ReceivingRuntime:
    return cast(ReceivingRuntime, session.info["receiving_runtime"])


def local_today() -> date:
    return datetime.now(UTC).astimezone(ZoneInfo("Asia/Dushanbe")).date()


async def prepare_report_lot(
    session: AsyncSession,
    *,
    factor: int = 30,
    cost: Decimal | None = Decimal("10.00"),
) -> PreparedLotResult:
    context = runtime(session)
    await session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{units_per_pack}',to_jsonb(CAST(:factor AS int))) "
            "WHERE id=:id"
        ),
        {"id": context.stock.catalog_id, "factor": factor},
    )
    return await PreparedUnitLedgerService(UnitLedgerRepository(session)).prepare_lot(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        catalog_id=context.stock.catalog_id,
        operation_id=uuid4(),
        packaging=UnitPackaging(unit="tablet", units_per_pack=factor, dispense_step=1),
        closed_packs=2,
        expires_at=local_today() + timedelta(days=180),
        allowed_branch_ids={context.stock.branch_id},
        purchase_cost_per_pack=cost,
    )


async def dispense_report_units(session: AsyncSession, quantity: int, *, factor: int = 30) -> UUID:
    context = runtime(session)
    result = await PreparedUnitLedgerService(UnitLedgerRepository(session)).dispense(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        catalog_id=context.stock.catalog_id,
        operation_id=uuid4(),
        packaging=UnitPackaging(unit="tablet", units_per_pack=factor, dispense_step=1),
        sale_format="unit",
        quantity=quantity,
        today=local_today(),
        allowed_branch_ids={context.stock.branch_id},
    )
    return result.operation_id


async def report_current(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
) -> UnitStockReport:
    context = runtime(session)
    return await UnitStockReportService(UnitStockReportRepository(session)).read_stock_on_date(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        on_date=local_today(),
        page=page,
        page_size=page_size,
    )


@pytest.mark.parametrize(
    "factor,first_cost",
    [(3, Decimal("3.33")), (7, Decimal("1.42")), (30, Decimal("0.33"))],
)
async def test_remaining_cost_conserves_original_physical_pack_amount_and_last_cent(
    db_session: AsyncSession,
    factor: int,
    first_cost: Decimal,
) -> None:
    prepared = await prepare_report_lot(db_session, factor=factor)
    before = await report_current(db_session)
    assert before.summary.lot_count == 1
    assert before.items[0].lot_id == prepared.lot_id
    assert before.items[0].closed_packs == 2
    assert before.items[0].opened_units == 0
    assert before.items[0].base_units == factor * 2
    assert before.items[0].remaining_cost == Decimal("20.00")
    await dispense_report_units(db_session, 1, factor=factor)
    after_first = await report_current(db_session)
    row = after_first.items[0]
    assert row.closed_packs == 1 and row.opened_units == factor - 1
    assert row.base_units == factor * 2 - 1
    assert row.packaging.units_per_pack == factor
    assert row.remaining_cost == Decimal("20.00") - first_cost
    await dispense_report_units(db_session, factor - 1, factor=factor)
    after_pack = await report_current(db_session)
    assert after_pack.items[0].closed_packs == 1
    assert after_pack.items[0].opened_units == 0
    assert after_pack.items[0].base_units == factor
    assert after_pack.items[0].remaining_cost == Decimal("10.00")
    assert after_pack.summary.known_remaining_cost == Decimal("10.00")


async def test_historical_cutoff_uses_entries_instead_of_current_projection_or_retail_prices(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    await prepare_report_lot(db_session)
    first_operation = await dispense_report_units(db_session, 1)
    first_timestamp = await db_session.scalar(
        text("SELECT min(created_at) FROM unit_stock_entry WHERE operation_id=:id"),
        {"id": first_operation},
    )
    assert isinstance(first_timestamp, datetime)
    repository = UnitStockReportRepository(db_session)
    before = await repository.fetch_stock_snapshot(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        cutoff_exclusive=first_timestamp,
    )
    assert before.items[0].closed_packs == 2
    assert before.items[0].opened_units == 0
    assert before.items[0].base_units == 60
    assert before.items[0].remaining_cost == Decimal("20.00")
    await dispense_report_units(db_session, 29)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=99.00,unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{unit_price}','\"9.00\"') WHERE id=:id"
        ),
        {"id": context.stock.catalog_id},
    )
    historical = await repository.fetch_stock_snapshot(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        cutoff_exclusive=first_timestamp + timedelta(microseconds=1),
    )
    assert historical.items[0].closed_packs == 1
    assert historical.items[0].opened_units == 29
    assert historical.items[0].base_units == 59
    assert historical.items[0].remaining_cost == Decimal("19.67")
    assert (await report_current(db_session)).items[0].remaining_cost == Decimal("10.00")


async def test_unknown_cost_is_not_zero_and_summary_preserves_known_subtotal(
    db_session: AsyncSession,
) -> None:
    unknown = await prepare_report_lot(db_session, cost=None)
    zero = await prepare_report_lot(db_session, cost=Decimal("0.00"))
    known = await prepare_report_lot(db_session)
    report = await report_current(db_session)
    rows = {row.lot_id: row for row in report.items}
    assert rows[unknown.lot_id].cost_status == "unknown"
    assert rows[unknown.lot_id].remaining_cost is None
    assert rows[zero.lot_id].cost_status == "known"
    assert rows[zero.lot_id].remaining_cost == Decimal("0.00")
    assert rows[known.lot_id].remaining_cost == Decimal("20.00")
    assert report.summary.cost_status == "partial"
    assert report.summary.unknown_cost_lot_count == 1
    assert report.summary.known_cost_lot_count == 2
    assert report.summary.known_remaining_cost == Decimal("20.00")


async def test_report_separates_received_and_standalone_sources_without_legacy_stock(
    db_session: AsyncSession,
) -> None:
    received = await seed_received(db_session)
    prepared = await prepare_report_lot(db_session, cost=Decimal("20.00"))
    report = await report_current(db_session)
    assert report.summary.received_lot_count == report.summary.prepared_lot_count == 1
    assert report.summary.lot_count == 2
    kinds = {row.lot_id: row.source_kind for row in report.items}
    assert kinds[prepared.lot_id] == "prepared"
    accepted_id = await db_session.scalar(
        text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
        {"id": received.item_ids[0]},
    )
    assert kinds[accepted_id] == "received"
    assert report.summary.known_remaining_cost == Decimal("60.00")
    assert report.summary.received_known_remaining_cost == Decimal("20.00")
    assert report.summary.prepared_known_remaining_cost == Decimal("40.00")


async def test_reports_only_reader_can_read_balances_but_cost_is_concealed_everywhere(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await seed_received(db_session)
    await prepare_report_lot(db_session)
    await prepare_report_lot(db_session, cost=None)
    await db_session.commit()
    reader = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=("reports.view",),
    )
    await set_receiving_context(db_session, context.stock, reader)
    report = await UnitStockReportService(UnitStockReportRepository(db_session)).read_stock_on_date(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=reader,
        allowed_branch_ids={context.stock.branch_id},
        on_date=local_today(),
    )
    assert report.summary.lot_count == 3
    assert report.summary.received_lot_count == 1
    assert report.summary.prepared_lot_count == 2
    assert report.summary.cost_status == "hidden"
    assert report.summary.known_cost_lot_count is None
    assert report.summary.unknown_cost_lot_count is None
    assert report.summary.known_remaining_cost is None
    assert report.summary.received_known_remaining_cost is None
    assert report.summary.prepared_known_remaining_cost is None
    assert all(row.cost_status == "hidden" and row.remaining_cost is None for row in report.items)


async def test_reports_and_cost_reader_needs_no_inventory_or_pos_permission(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await prepare_report_lot(db_session)
    await db_session.commit()
    reader = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=("reports.view", "batches.view_costs"),
    )
    await set_receiving_context(db_session, context.stock, reader)
    report = await UnitStockReportService(UnitStockReportRepository(db_session)).read_stock_on_date(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=reader,
        allowed_branch_ids={context.stock.branch_id},
        on_date=local_today(),
    )
    assert report.items[0].remaining_cost == Decimal("20.00")
    assert report.summary.known_remaining_cost == Decimal("20.00")


async def test_actor_reports_permission_and_branch_are_checked_before_query(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await prepare_report_lot(db_session)
    await db_session.commit()
    viewer = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=("pos.sell",),
    )
    service = UnitStockReportService(UnitStockReportRepository(db_session))
    for tenant, branch, actor, allowed in (
        (context.stock.tenant_id, context.stock.branch_id, uuid4(), {context.stock.branch_id}),
        (uuid4(), context.stock.branch_id, context.actor_id, {context.stock.branch_id}),
        (context.stock.tenant_id, uuid4(), context.actor_id, set()),
    ):
        with pytest.raises(PermissionDeniedError):
            await service.read_stock_on_date(
                tenant_id=tenant,
                branch_id=branch,
                actor_id=actor,
                allowed_branch_ids=allowed,
                on_date=local_today(),
            )
    await set_receiving_context(db_session, context.stock, viewer)
    with pytest.raises(PermissionDeniedError):
        await service.read_stock_on_date(
            tenant_id=context.stock.tenant_id,
            branch_id=context.stock.branch_id,
            actor_id=viewer,
            allowed_branch_ids={context.stock.branch_id},
            on_date=local_today(),
        )


async def test_revoked_assignment_stops_existing_report_reader(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await prepare_report_lot(db_session)
    assert (await report_current(db_session)).summary.lot_count == 1
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "UPDATE user_assignment SET is_active=false "
                "WHERE user_id=:actor AND tenant_id=:tenant"
            ),
            {"actor": context.actor_id, "tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    with pytest.raises(PermissionDeniedError):
        await report_current(db_session)


async def test_support_session_cannot_spoof_report_actor(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await prepare_report_lot(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(
                text(
                    "SELECT set_config('app.support_session','true',true), "
                    "set_config('app.tenant_id',:tenant,true),"
                    "set_config('app.user_id',:actor,true)"
                ),
                {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
            )
            with pytest.raises(PermissionDeniedError):
                await UnitStockReportService(UnitStockReportRepository(support)).read_stock_on_date(
                    tenant_id=context.stock.tenant_id,
                    branch_id=context.stock.branch_id,
                    actor_id=context.actor_id,
                    allowed_branch_ids={context.stock.branch_id},
                    on_date=local_today(),
                )


async def test_pagination_has_stable_lot_order_and_full_summary_on_every_page(
    db_session: AsyncSession,
) -> None:
    identifiers = [(await prepare_report_lot(db_session)).lot_id for _ in range(3)]
    pages = [await report_current(db_session, page=page, page_size=1) for page in range(1, 5)]
    assert [page.items[0].lot_id for page in pages[:3]] == sorted(identifiers)
    assert pages[3].items == ()
    assert all(page.summary.lot_count == 3 for page in pages)
    assert all(page.summary.known_remaining_cost == Decimal("60.00") for page in pages)


async def test_empty_past_date_and_exhausted_lot_are_excluded_from_stock(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    await prepare_report_lot(db_session)
    past = await UnitStockReportService(UnitStockReportRepository(db_session)).read_stock_on_date(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        on_date=local_today() - timedelta(days=1),
    )
    assert past.summary.lot_count == 0 and past.items == ()
    assert past.summary.known_remaining_cost == Decimal("0.00")
    await dispense_report_units(db_session, 60)
    exhausted = await report_current(db_session)
    assert exhausted.summary.lot_count == 0
    assert exhausted.summary.received_lot_count == exhausted.summary.prepared_lot_count == 0
    assert exhausted.items == ()
    assert exhausted.summary.known_remaining_cost == Decimal("0.00")
    assert exhausted.summary.received_known_remaining_cost == Decimal("0.00")
    assert exhausted.summary.prepared_known_remaining_cost == Decimal("0.00")


@pytest.mark.parametrize(
    "day,zone,cutoff",
    [
        (date(2026, 10, 4), "Asia/Dushanbe", datetime(2026, 10, 4, 19, tzinfo=UTC)),
        (date(2026, 3, 29), "Europe/Berlin", datetime(2026, 3, 29, 22, tzinfo=UTC)),
    ],
)
async def test_local_report_day_uses_exclusive_midnight_with_timezone_and_dst(
    db_session: AsyncSession,
    day: date,
    zone: str,
    cutoff: datetime,
) -> None:
    context = runtime(db_session)
    report = await UnitStockReportService(UnitStockReportRepository(db_session)).read_stock_on_date(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        on_date=day,
        tz=zone,
    )
    assert report.cutoff_exclusive == cutoff
    assert report.timezone == zone and report.on_date == day


async def test_report_cost_arithmetic_does_not_depend_on_ambient_decimal_context(
    db_session: AsyncSession,
) -> None:
    await prepare_report_lot(db_session)
    await dispense_report_units(db_session, 1)
    with localcontext() as ambient:
        ambient.prec = 1
        ambient.traps[Inexact] = True
        report = await report_current(db_session)
    assert report.items[0].remaining_cost == Decimal("19.67")
    assert report.summary.known_remaining_cost == Decimal("19.67")
