"""Prepared line snapshots atomically record explicit retail and original cost."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal, Inexact, localcontext
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.catalog.unit_sales import SaleFormat
from app.domains.inventory.unit_stock import UnitStockShortage
from app.domains.pos.unit_line_models import UnitSaleLine
from app.domains.pos.unit_line_repository import UnitSaleLineRepository
from app.domains.pos.unit_line_service import (
    PreparedUnitSaleLineResult,
    PreparedUnitSaleLineService,
)
from tests.domains.inventory.test_unit_cost_ledger import prepare_priced, take
from tests.domains.inventory.test_unit_ledger import TODAY, LedgerContext, seed_ledger_context


async def seed_line_context(session: AsyncSession, *, packs: int = 2) -> LedgerContext:
    context = await seed_ledger_context(session)
    await session.execute(
        text("UPDATE tenant_catalog SET base_price=25.00 WHERE id=:catalog"),
        {"catalog": context.catalog_id},
    )
    await prepare_priced(session, context, packs=packs)
    return context


async def post(
    session: AsyncSession,
    context: LedgerContext,
    *,
    quantity: int = 1,
    sale_format: SaleFormat = "unit",
    discount: Decimal = Decimal("0.00"),
    operation_id: UUID | None = None,
) -> PreparedUnitSaleLineResult:
    return await PreparedUnitSaleLineService(UnitSaleLineRepository(session)).post_line(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=operation_id or uuid4(),
        sale_format=sale_format,
        quantity=quantity,
        discount_amount=discount,
        today=TODAY,
        allowed_branch_ids={context.branch_id},
    )


async def assert_no_line_commands(session: AsyncSession, context: LedgerContext) -> None:
    for table in ("pos_unit_sale_command", "pos_unit_sale_line"):
        assert (
            await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.tenant_id},
            )
            == 0
        )
    assert (
        await session.scalar(
            text(
                "SELECT count(*) FROM unit_stock_operation "
                "WHERE tenant_id=:tenant AND kind='dispense'"
            ),
            {"tenant": context.tenant_id},
        )
        == 0
    )


async def test_pack_and_unit_use_explicit_server_prices_with_original_cost(
    db_session: AsyncSession,
) -> None:
    context = await seed_line_context(db_session)
    unit = await post(db_session, context, quantity=3, discount=Decimal("0.25"))
    pack = await post(db_session, context, sale_format="pack", discount=Decimal("1.00"))
    assert unit.snapshot.original_retail_unit_price == Decimal("1.00")
    assert unit.snapshot.gross_amount == Decimal("3.00")
    assert unit.snapshot.discount_amount == Decimal("0.25")
    assert unit.snapshot.net_amount == Decimal("2.75")
    assert unit.snapshot.base_units == 3
    assert unit.snapshot.original_cost_amount == Decimal("1.00")
    assert pack.snapshot.original_retail_unit_price == Decimal("25.00")
    assert pack.snapshot.gross_amount == Decimal("25.00")
    assert pack.snapshot.net_amount == Decimal("24.00")
    assert pack.snapshot.base_units == 30
    assert pack.snapshot.original_cost_amount == Decimal("10.00")
    for result in (unit, pack):
        assert result.snapshot.tenant_id == context.tenant_id
        assert result.snapshot.branch_id == context.branch_id
        assert result.snapshot.catalog_id == context.catalog_id
        assert result.snapshot.currency == "TJS"
        assert result.snapshot.version == result.snapshot.money_version == 1
        assert (
            sum(part.amount for part in result.cost_slices) == result.snapshot.original_cost_amount
        )
        assert sum(part.base_units for part in result.cost_slices) == result.snapshot.base_units
    for table in ("sale", "sale_item", "sale_payment", "shift", "pos_command"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.tenant_id},
            )
            == 0
        )


async def test_replay_retains_prices_packaging_discount_and_cost_after_catalog_edit(
    db_session: AsyncSession,
) -> None:
    context = await seed_line_context(db_session)
    operation_id = uuid4()
    original = await post(
        db_session, context, quantity=7, discount=Decimal("0.10"), operation_id=operation_id
    )
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=40.00, unit_sale_preparation="
            "jsonb_set(jsonb_set(unit_sale_preparation,'{unit_price}','\"2.00\"'),"
            "'{units_per_pack}','60') WHERE id=:catalog"
        ),
        {"catalog": context.catalog_id},
    )
    repeated = await PreparedUnitSaleLineService(UnitSaleLineRepository(db_session)).post_line(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=operation_id,
        sale_format="unit",
        quantity=7,
        discount_amount=Decimal("0.1"),
        today=TODAY + timedelta(days=90),
        allowed_branch_ids={context.branch_id},
    )
    assert repeated == original
    assert repeated.snapshot.units_per_pack == 30
    assert repeated.snapshot.original_retail_unit_price == Decimal("1.00")
    for quantity, discount, sale_format in (
        (8, Decimal("0.10"), "unit"),
        (7, Decimal("0.11"), "unit"),
        (7, Decimal("0.10"), "pack"),
    ):
        with pytest.raises(ConflictError):
            await post(
                db_session,
                context,
                quantity=quantity,
                discount=discount,
                sale_format=sale_format,
                operation_id=operation_id,
            )


@pytest.mark.parametrize("discount", [Decimal("-0.01"), Decimal("1.00"), Decimal("1.001")])
async def test_invalid_discount_cannot_claim_or_dispense(
    db_session: AsyncSession, discount: Decimal
) -> None:
    context = await seed_line_context(db_session)
    with pytest.raises(ValueError):
        await post(db_session, context, discount=discount)
    await assert_no_line_commands(db_session, context)


async def test_maximum_gross_and_one_cent_net_are_exact_under_hostile_decimal_context(
    db_session: AsyncSession,
) -> None:
    context = await seed_line_context(db_session, packs=3)
    await db_session.execute(
        text("UPDATE tenant_catalog SET base_price=999999999999.99 WHERE id=:catalog"),
        {"catalog": context.catalog_id},
    )
    with localcontext() as ambient:
        ambient.prec = 1
        ambient.traps[Inexact] = True
        result = await post(
            db_session, context, sale_format="pack", discount=Decimal("999999999999.98")
        )
    assert result.snapshot.gross_amount == Decimal("999999999999.99")
    assert result.snapshot.net_amount == Decimal("0.01")
    operation_id = uuid4()
    with pytest.raises(ValueError):
        await post(db_session, context, sale_format="pack", quantity=2, operation_id=operation_id)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_sale_command WHERE id=:id"), {"id": operation_id}
        )
        == 0
    )


async def test_unknown_cost_and_mixed_known_unknown_cost_roll_back_every_pick(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    await prepare_priced(db_session, context, cost=None)
    await prepare_priced(db_session, context)
    with pytest.raises(BusinessRuleError):
        await post(db_session, context, quantity=31)
    await assert_no_line_commands(db_session, context)
    assert (
        await db_session.scalar(
            text("SELECT sum(closed_packs) FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": context.tenant_id},
        )
        == 2
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_opened_pack WHERE tenant_id=:tenant"),
            {"tenant": context.tenant_id},
        )
        == 0
    )


async def test_low_stock_failure_rolls_back_command_then_same_request_can_succeed(
    db_session: AsyncSession,
) -> None:
    context = await seed_line_context(db_session, packs=1)
    operation_id = uuid4()
    with pytest.raises(UnitStockShortage):
        await post(db_session, context, quantity=31, operation_id=operation_id)
    await assert_no_line_commands(db_session, context)
    await prepare_priced(db_session, context)
    result = await post(db_session, context, quantity=31, operation_id=operation_id)
    assert result.snapshot.base_units == 31


async def test_new_line_cannot_reuse_an_existing_raw_stock_dispense(
    db_session: AsyncSession,
) -> None:
    context = await seed_line_context(db_session, packs=1)
    command_id = uuid4()
    stock_id = uuid5(NAMESPACE_URL, f"aurum-unit-line-stock:{context.tenant_id}:{command_id}")
    previous = await take(db_session, context, 1, operation_id=stock_id)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{unit_price}','\"9.00\"') WHERE id=:catalog"
        ),
        {"catalog": context.catalog_id},
    )
    with pytest.raises(ConflictError):
        await post(db_session, context, operation_id=command_id)
    for table in ("pos_unit_sale_line", "pos_unit_sale_command"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.tenant_id},
            )
            == 0
        )
    assert await take(db_session, context, 1, operation_id=stock_id) == previous
    assert (
        await db_session.scalar(
            text("SELECT sum(remaining_units) FROM unit_stock_opened_pack WHERE tenant_id=:tenant"),
            {"tenant": context.tenant_id},
        )
        == 29
    )


async def test_outer_rollback_removes_line_stock_and_command_together(
    db_session: AsyncSession,
) -> None:
    context = await seed_line_context(db_session, packs=1)
    operation_id = uuid4()
    outer = await db_session.begin_nested()
    await post(db_session, context, quantity=7, operation_id=operation_id)
    await outer.rollback()
    await assert_no_line_commands(db_session, context)
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": context.tenant_id},
        )
        == 1
    )
    result = await post(db_session, context, quantity=7, operation_id=operation_id)
    assert result.cost_slices[0].unit_offset == 0


async def test_line_and_command_are_immutable_and_audited(db_session: AsyncSession) -> None:
    context = await seed_line_context(db_session)
    result = await post(db_session, context)
    for statement in (
        "UPDATE pos_unit_sale_line SET net_amount=99 WHERE id=:line",
        "UPDATE pos_unit_sale_line SET original_cost_amount=0 WHERE id=:line",
        "UPDATE pos_unit_sale_line SET branch_id=:branch WHERE id=:line",
        "DELETE FROM pos_unit_sale_line WHERE id=:line",
        "UPDATE pos_unit_sale_command SET quantity=99 WHERE id=:command",
        "DELETE FROM pos_unit_sale_command WHERE id=:command",
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(statement),
                    {
                        "line": result.snapshot.id,
                        "command": result.snapshot.command_id,
                        "branch": uuid4(),
                    },
                )
    assert await post(db_session, context, operation_id=result.snapshot.command_id) == result
    audited = set(
        (
            await db_session.execute(
                text("SELECT DISTINCT table_name FROM audit_log WHERE tenant_id=:tenant"),
                {"tenant": context.tenant_id},
            )
        ).scalars()
    )
    assert {"pos_unit_sale_command", "pos_unit_sale_line"} <= audited


class ForgedLineRepository(UnitSaleLineRepository):
    def __init__(self, session: AsyncSession, field: str, foreign_catalog: UUID) -> None:
        super().__init__(session)
        self.field = field
        self.foreign_catalog = foreign_catalog

    async def insert_line(self, line: UnitSaleLine) -> None:
        if self.field == "net":
            line.net_amount = Decimal("99.00")
        elif self.field == "cost":
            line.original_cost_amount = Decimal("0.00")
        else:
            line.catalog_id = self.foreign_catalog
        await super().insert_line(line)
        await self.session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


@pytest.mark.parametrize("field", ["net", "cost", "scope"])
async def test_database_rejects_forged_line_without_changing_stock(
    db_session: AsyncSession, field: str
) -> None:
    context = await seed_line_context(db_session)
    foreign = await seed_ledger_context(db_session)
    with pytest.raises(DBAPIError):
        await PreparedUnitSaleLineService(
            ForgedLineRepository(db_session, field, foreign.catalog_id)
        ).post_line(
            tenant_id=context.tenant_id,
            branch_id=context.branch_id,
            catalog_id=context.catalog_id,
            operation_id=uuid4(),
            sale_format="unit",
            quantity=1,
            today=TODAY,
            allowed_branch_ids={context.branch_id},
        )
    await assert_no_line_commands(db_session, context)
    assert (
        await db_session.scalar(
            text("SELECT sum(closed_packs) FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": context.tenant_id},
        )
        == 2
    )


@pytest_asyncio.fixture
async def committed_line_stock(
    db_engine: AsyncEngine,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], LedgerContext]]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory.begin() as session:
        assert str(await session.scalar(text("SELECT current_database()"))).endswith("_test")
        await session.execute(text("SELECT set_config('app.support_session','true',true)"))
        context = await seed_line_context(session, packs=1)
    yield factory, context


async def concurrent_line(
    factory: async_sessionmaker[AsyncSession], context: LedgerContext, operation_id: UUID
) -> PreparedUnitSaleLineResult | UnitStockShortage:
    try:
        async with factory.begin() as session:
            await session.execute(text("SELECT set_config('app.support_session','true',true)"))
            return await post(session, context, operation_id=operation_id)
    except UnitStockShortage as error:
        return error


async def test_simultaneous_line_retry_has_one_line_and_one_stock_charge(
    committed_line_stock: tuple[async_sessionmaker[AsyncSession], LedgerContext],
) -> None:
    factory, context = committed_line_stock
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(*(concurrent_line(factory, context, operation_id) for _ in range(4))),
        timeout=15,
    )
    assert isinstance(results[0], PreparedUnitSaleLineResult)
    assert all(result == results[0] for result in results)
    async with factory.begin() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM pos_unit_sale_line WHERE tenant_id=:tenant"),
                {"tenant": context.tenant_id},
            )
            == 1
        )
        assert (
            await session.scalar(
                text(
                    "SELECT sum(remaining_units) FROM unit_stock_opened_pack "
                    "WHERE tenant_id=:tenant"
                ),
                {"tenant": context.tenant_id},
            )
            == 29
        )


async def test_simultaneous_last_unit_has_one_successful_line(
    committed_line_stock: tuple[async_sessionmaker[AsyncSession], LedgerContext],
) -> None:
    factory, context = committed_line_stock
    async with factory.begin() as session:
        await session.execute(text("SELECT set_config('app.support_session','true',true)"))
        first = await post(session, context, quantity=29)
    results = await asyncio.wait_for(
        asyncio.gather(*(concurrent_line(factory, context, uuid4()) for _ in range(2))), timeout=15
    )
    winners = [result for result in results if isinstance(result, PreparedUnitSaleLineResult)]
    assert len(winners) == 1
    assert sum(isinstance(result, UnitStockShortage) for result in results) == 1
    assert first.snapshot.original_cost_amount + winners[
        0
    ].snapshot.original_cost_amount == Decimal("10.00")
