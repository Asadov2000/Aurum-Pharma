"""Retail snapshots remain atomic and replayable independently of live catalog prices."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_dispensing_v2_retail import (
    UnitCartRetailResultV2,
    UnitPricedCartLineV2,
)
from app.domains.inventory.unit_dispensing_v2_service import PhysicalUnitLedgerV2Service
from app.domains.inventory.unit_ledger_v2_models import UnitStockCartLineV2
from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2
from tests.domains.incoming.test_unit_acceptance import ReceivingRuntime, set_receiving_context
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_cost_ledger_v2 import priced_source
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    PhysicalSource,
    cart_line,
    dispense,
    remaining,
)

db_session = receiving_session_fixture


@pytest.mark.parametrize("discount", [Decimal("NaN"), Decimal("Infinity"), Decimal("0.001")])
def test_priced_line_rejects_nonfinite_or_subcent_discount(discount: Decimal) -> None:
    packaging = UnitPackagingV2(
        unit="tablet", units_per_pack=30, units_per_blister=10, dispense_step=1
    )
    with pytest.raises(ValidationError):
        UnitPricedCartLineV2(
            id=uuid4(),
            catalog_id=uuid4(),
            packaging=packaging,
            sale_format="unit",
            quantity=1,
            discount_amount=discount,
        )


async def retail_source(
    session: AsyncSession, *, quantity: int = 1, cost: Decimal = Decimal("10.00")
) -> PhysicalSource:
    source = await priced_source(session, quantity=quantity, price=cost)
    await session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=60.00,unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{unit_price}','\"2.00\"'::jsonb) WHERE id=:id"
        ),
        {"id": source.incoming.stock.catalog_id},
    )
    return source


def retail_line(
    source: PhysicalSource,
    sale_format: SaleFormatV2,
    quantity: int,
    discount: Decimal = Decimal("0.00"),
) -> UnitPricedCartLineV2:
    return UnitPricedCartLineV2(
        **cart_line(source, sale_format, quantity).model_dump(), discount_amount=discount
    )


async def dispense_retail(
    session: AsyncSession,
    source: PhysicalSource,
    lines: tuple[UnitPricedCartLineV2, ...],
    *,
    command_id: UUID | None = None,
) -> UnitCartRetailResultV2:
    context = source.incoming
    service = PhysicalUnitLedgerV2Service(UnitDispensingV2Repository(session))
    return await service.dispense_priced_cart(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        command_id=command_id or uuid4(),
        lines=lines,
    )


@pytest.mark.parametrize(
    ("sale_format", "quantity", "units", "unit_price", "gross"),
    [
        ("pack", 1, 30, Decimal("60.00"), Decimal("60.00")),
        ("blister", 2, 20, Decimal("20.00"), Decimal("40.00")),
        ("unit", 3, 3, Decimal("2.00"), Decimal("6.00")),
    ],
)
async def test_all_formats_snapshot_explicit_retail_prices(
    db_session: AsyncSession,
    sale_format: SaleFormatV2,
    quantity: int,
    units: int,
    unit_price: Decimal,
    gross: Decimal,
) -> None:
    source = await retail_source(db_session)
    line = retail_line(source, sale_format, quantity, Decimal("1.00"))
    result = await dispense_retail(db_session, source, (line,))
    assert len(result.lines) == 1
    snapshot = result.lines[0]
    assert snapshot.id == line.id
    assert snapshot.catalog_id == line.catalog_id
    assert snapshot.sale_format == sale_format
    assert snapshot.quantity == quantity
    assert snapshot.base_units == units
    assert snapshot.original_retail_unit_price == unit_price
    assert snapshot.gross_amount == gross
    assert snapshot.discount_amount == Decimal("1.00")
    assert snapshot.net_amount == result.total_amount == gross - Decimal("1.00")
    assert result.currency == "TJS" and result.money_version == 1
    assert result.cost_result.command_id == result.command_id
    assert await remaining(db_session, source) == 30 - units


async def test_pack_price_is_not_inferred_from_unit_price(db_session: AsyncSession) -> None:
    source = await retail_source(db_session)
    await db_session.execute(
        text("UPDATE tenant_catalog SET base_price=75.00 WHERE id=:id"),
        {"id": source.incoming.stock.catalog_id},
    )
    result = await dispense_retail(db_session, source, (retail_line(source, "pack", 1),))
    assert result.lines[0].original_retail_unit_price == result.total_amount == Decimal("75.00")


async def test_discount_replay_survives_exhaustion_and_catalog_deactivation(
    db_session: AsyncSession,
) -> None:
    source = await retail_source(db_session)
    lines = (retail_line(source, "pack", 1, Decimal("5.00")),)
    result = await dispense_retail(db_session, source, lines)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET is_active=false,base_price=99,"
            "unit_sale_preparation=NULL WHERE id=:id"
        ),
        {"id": source.incoming.stock.catalog_id},
    )
    assert await dispense_retail(db_session, source, lines, command_id=result.command_id) == result
    changed = lines[0].model_copy(update={"discount_amount": Decimal("6.00")})
    with pytest.raises(ConflictError):
        await dispense_retail(db_session, source, (changed,), command_id=result.command_id)
    assert await remaining(db_session, source) == 0


async def test_signed_zero_discount_replays_as_canonical_zero(db_session: AsyncSession) -> None:
    source = await retail_source(db_session)
    negative_zero = retail_line(source, "unit", 1, Decimal("-0.00"))
    assert negative_zero.discount_amount == Decimal("0.00")
    assert not negative_zero.discount_amount.is_signed()
    result = await dispense_retail(db_session, source, (negative_zero,))
    canonical = UnitPricedCartLineV2.model_validate(
        {**negative_zero.model_dump(), "discount_amount": Decimal("0.00")}
    )
    assert (
        await dispense_retail(db_session, source, (canonical,), command_id=result.command_id)
        == result
    )
    assert result.lines[0].discount_amount == Decimal("0.00")
    assert await remaining(db_session, source) == 29


@pytest.mark.parametrize("priced_first", [True, False])
async def test_command_cannot_switch_between_priced_and_unpriced(
    db_session: AsyncSession, priced_first: bool
) -> None:
    source = await retail_source(db_session)
    priced = retail_line(source, "unit", 1)
    physical = UnitCartLineV2.model_validate(priced.model_dump(exclude={"discount_amount"}))
    command_id = uuid4()
    if priced_first:
        await dispense_retail(db_session, source, (priced,), command_id=command_id)
        with pytest.raises(ConflictError):
            await dispense(db_session, source, (physical,), command_id=command_id)
    else:
        await dispense(db_session, source, (physical,), command_id=command_id)
        with pytest.raises(ConflictError):
            await dispense_retail(db_session, source, (priced,), command_id=command_id)
    assert await remaining(db_session, source) == 29


async def test_invalid_last_discount_rolls_back_whole_cart(db_session: AsyncSession) -> None:
    source = await retail_source(db_session)
    command_id = uuid4()
    lines = (
        retail_line(source, "unit", 1),
        retail_line(source, "unit", 1, Decimal("2.01")),
    )
    with pytest.raises(BusinessRuleError):
        await dispense_retail(db_session, source, lines, command_id=command_id)
    assert await remaining(db_session, source) == 30
    for table in (
        "unit_stock_cart_command_v2",
        "unit_stock_cart_line_v2",
        "unit_stock_movement_v2",
    ):
        column = "id" if table == "unit_stock_cart_command_v2" else "command_id"
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {column}=:id"), {"id": command_id}
            )
            == 0
        )
    retry = await dispense_retail(db_session, source, (lines[0],), command_id=command_id)
    assert retry.total_amount == Decimal("2.00")


async def test_concurrent_exact_replay_saves_one_retail_snapshot(db_session: AsyncSession) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    source = await retail_source(db_session)
    command_id = uuid4()
    lines = (retail_line(source, "unit", 7, Decimal("1.50")),)
    await db_session.commit()

    async def attempt() -> UnitCartRetailResultV2:
        async with runtime.factory.begin() as session:
            await set_receiving_context(session, runtime.stock, runtime.actor_id)
            return await dispense_retail(session, source, lines, command_id=command_id)

    results = await asyncio.wait_for(asyncio.gather(*(attempt() for _ in range(4))), timeout=15)
    assert all(result == results[0] for result in results)
    await set_receiving_context(db_session, runtime.stock, runtime.actor_id)
    assert await remaining(db_session, source) == 23
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_cart_line_v2 WHERE command_id=:id"),
            {"id": command_id},
        )
        == 1
    )
    assert results[0].total_amount == Decimal("12.50")


async def test_replay_rechecks_branch_and_actor(db_session: AsyncSession) -> None:
    source = await retail_source(db_session)
    lines = (retail_line(source, "unit", 1),)
    result = await dispense_retail(db_session, source, lines)
    service = PhysicalUnitLedgerV2Service(UnitDispensingV2Repository(db_session))
    for actor, scope in (
        (uuid4(), {source.incoming.stock.branch_id}),
        (source.incoming.actor_id, set()),
    ):
        with pytest.raises(PermissionDeniedError):
            await service.dispense_priced_cart(
                tenant_id=source.incoming.stock.tenant_id,
                branch_id=source.incoming.stock.branch_id,
                command_id=result.command_id,
                actor_id=actor,
                allowed_branch_ids=scope,
                lines=lines,
            )
    assert await remaining(db_session, source) == 29


async def test_known_zero_cost_remains_distinct_from_retail(db_session: AsyncSession) -> None:
    source = await retail_source(db_session, cost=Decimal("0.00"))
    result = await dispense_retail(db_session, source, (retail_line(source, "pack", 1),))
    assert result.total_amount == Decimal("60.00")
    assert result.cost_result.total_amount == Decimal("0.00")
    assert result.cost_result.cost_slices
    assert all(part.amount == Decimal("0.00") for part in result.cost_result.cost_slices)


@pytest.mark.parametrize("priced", [True, False])
async def test_database_rejects_forged_retail_snapshots(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, priced: bool
) -> None:
    source = await retail_source(db_session)
    original = UnitDispensingV2Repository.insert_cart_lines

    async def forged(repo: UnitDispensingV2Repository, lines: list[UnitStockCartLineV2]) -> None:
        lines[0].original_retail_unit_price = Decimal("1.00")
        lines[0].gross_amount = Decimal("1.00")
        lines[0].discount_amount = Decimal("0.00")
        lines[0].net_amount = Decimal("1.00")
        await original(repo, lines)

    monkeypatch.setattr(UnitDispensingV2Repository, "insert_cart_lines", forged)
    command_id = uuid4()
    with pytest.raises(DBAPIError) as error:
        if priced:
            await dispense_retail(
                db_session, source, (retail_line(source, "unit", 1),), command_id=command_id
            )
        else:
            await dispense(
                db_session, source, (cart_line(source, "unit", 1),), command_id=command_id
            )
    assert error.value.orig.sqlstate == "23514"
    assert await remaining(db_session, source) == 30
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_cart_command_v2 WHERE id=:id"),
            {"id": command_id},
        )
        == 0
    )


async def test_cross_tenant_context_cannot_read_or_replay_retail(db_session: AsyncSession) -> None:
    source = await retail_source(db_session)
    lines = (retail_line(source, "unit", 1),)
    result = await dispense_retail(db_session, source, lines)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    try:
        for table in ("unit_stock_cart_command_v2", "unit_stock_cart_line_v2"):
            column = "id" if table == "unit_stock_cart_command_v2" else "command_id"
            assert (
                await db_session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE {column}=:id"),
                    {"id": result.command_id},
                )
                == 0
            )
        with pytest.raises(PermissionDeniedError):
            await dispense_retail(db_session, source, lines, command_id=result.command_id)
    finally:
        await set_receiving_context(db_session, source.incoming.stock, source.incoming.actor_id)
    assert await remaining(db_session, source) == 29


async def test_retail_audit_masks_prices(db_session: AsyncSession) -> None:
    source = await retail_source(db_session)
    result = await dispense_retail(db_session, source, (retail_line(source, "unit", 1),))
    events = (
        (
            await db_session.execute(
                text(
                    "SELECT new_values FROM audit_log WHERE table_name='unit_stock_cart_line_v2' "
                    "AND new_values->>'command_id'=:command"
                ),
                {"command": str(result.command_id)},
            )
        )
        .scalars()
        .all()
    )
    assert events
    for event in events:
        for field in (
            "original_retail_unit_price",
            "gross_amount",
            "discount_amount",
            "net_amount",
        ):
            assert event[field] == "***"
