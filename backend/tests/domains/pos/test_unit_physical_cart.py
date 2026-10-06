"""Physical draft formats preserve prices without spending cash or reserving stock."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.models import Sale
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartItemSnapshot, UnitCartService
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_cash_intent_service import SavedCashIntentV2
from app.domains.pos.unit_checkout_preparation import UnitCheckoutPreparationService
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_cart_quote import add_legacy, quote
from tests.domains.pos.test_unit_cash_intent import save
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def physical_draft(session: AsyncSession, *, blister: bool = True) -> Sale:
    sale = await seed_cart_draft(session)
    context = runtime(session)
    if blister:
        await session.execute(
            text(
                "UPDATE tenant_catalog SET unit_sale_preparation="
                "jsonb_set(unit_sale_preparation,'{units_per_blister}','10') WHERE id=:id"
            ),
            {"id": context.stock.catalog_id},
        )
    return sale


async def put_physical(
    session: AsyncSession,
    sale_id: UUID,
    *,
    item_id: UUID | None = None,
    position: int = 1,
    sale_format: SaleFormatV2 = "unit",
    quantity: int = 1,
    discount: Decimal = Decimal("0.00"),
) -> UnitCartItemSnapshot:
    context = runtime(session)
    return await UnitCartService(UnitCartRepository(session)).upsert_physical_item(
        tenant_id=context.stock.tenant_id,
        sale_id=sale_id,
        item_id=item_id or uuid4(),
        catalog_id=context.stock.catalog_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        position=position,
        sale_format=sale_format,
        quantity=quantity,
        discount_amount=discount,
    )


async def physical_request(session: AsyncSession, sale_id: UUID) -> CashConfirmationRequestV2:
    current = await quote(session, sale_id)
    return CashConfirmationRequestV2(
        operation_id=uuid4(),
        sale_id=sale_id,
        expected_revision_hash=current.revision_hash,
        expected_total_amount=current.total_amount,
        cash_tendered=current.total_amount + Decimal("5.00"),
    )


@pytest.mark.parametrize(
    ("sale_format", "quantity", "base_units", "gross"),
    [("pack", 2, 60, "50.00"), ("blister", 2, 20, "20.00"), ("unit", 2, 2, "2.00")],
)
async def test_physical_formats_quote_and_cash_have_no_stock_or_payment_effect(
    db_session: AsyncSession,
    sale_format: SaleFormatV2,
    quantity: int,
    base_units: int,
    gross: str,
) -> None:
    sale = await physical_draft(db_session)
    context = runtime(db_session)
    tables = (
        "unit_stock_entry",
        "unit_stock_movement_v2",
        "unit_stock_cart_command_v2",
        "sale_payment",
        "pos_payment_attempt",
    )
    before = {
        table: await db_session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
        for table in tables
    }
    stock_query = text(
        "SELECT to_jsonb(row) FROM unit_stock_lot row WHERE tenant_id=:tenant ORDER BY id"
    )
    stock_before = (
        (await db_session.execute(stock_query, {"tenant": context.stock.tenant_id})).scalars().all()
    )
    item = await put_physical(
        db_session,
        sale.id,
        sale_format=sale_format,
        quantity=quantity,
        discount=Decimal("0.01"),
    )
    assert item.physical_packaging is not None
    assert item.physical_packaging.units_per_blister == 10
    result = await quote(db_session, sale.id)
    line = result.lines[0]
    assert line.inventory_kind == "physical_unit"
    assert line.base_units == base_units and line.gross_amount == Decimal(gross)
    assert result.total_amount == Decimal(gross) - Decimal("0.01")
    assert MixedCartQuoteV2.model_validate_json(result.model_dump_json()) == result
    request = await physical_request(db_session, sale.id)
    original = await save(db_session, request)
    assert await save(db_session, request) == original
    assert SavedCashIntentV2.model_validate_json(original.model_dump_json()) == original
    assert original.confirmation.quote == result
    assert original.confirmation.change_amount == Decimal("5.00")
    for table, count in before.items():
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
            == count
        )
    assert await db_session.scalar(
        text("SELECT total_amount FROM sale WHERE id=:id"), {"id": sale.id}
    ) == Decimal("0.00")
    assert "cost" not in original.model_dump_json()
    assert (
        await db_session.execute(stock_query, {"tenant": context.stock.tenant_id})
    ).scalars().all() == stock_before


@pytest.mark.parametrize(
    "change", [{"base_units": 1}, {"gross_amount": "11.00", "net_amount": "11.00"}]
)
def test_physical_quote_contract_rejects_inconsistent_arithmetic(
    change: dict[str, object],
) -> None:
    payload = {
        "id": uuid4(),
        "position": 1,
        "catalog_id": uuid4(),
        "packaging": {
            "version": 2,
            "unit": "tablet",
            "units_per_pack": 30,
            "units_per_blister": 10,
            "dispense_step": 1,
        },
        "sale_format": "blister",
        "quantity": 1,
        "base_units": 10,
        "original_retail_unit_price": "10.00",
        "gross_amount": "10.00",
        "discount_amount": "0.00",
        "net_amount": "10.00",
    }
    assert PhysicalUnitDraftQuoteLineV2.model_validate(payload).base_units == 10
    with pytest.raises(ValidationError):
        PhysicalUnitDraftQuoteLineV2.model_validate({**payload, **change})


async def test_old_cart_snapshot_keeps_legacy_json_shape(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    await put_cart_item(db_session, sale.id)
    context = runtime(db_session)
    rows = await UnitCartService(UnitCartRepository(db_session)).list_items(
        tenant_id=context.stock.tenant_id,
        sale_id=sale.id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert rows[0].physical_packaging is None
    assert "physical_packaging" not in rows[0].model_dump()
    assert "physical_packaging" not in rows[0].model_dump_json()


async def test_pack_blister_and_units_can_share_cart_with_legacy_sale_item(
    db_session: AsyncSession,
) -> None:
    sale = await physical_draft(db_session)
    await add_legacy(db_session, sale)
    formats: tuple[SaleFormatV2, ...] = ("pack", "blister", "unit")
    for position, sale_format in enumerate(formats, 1):
        await put_physical(db_session, sale.id, position=position, sale_format=sale_format)
    current = await quote(db_session, sale.id)
    assert [line.inventory_kind for line in current.lines] == [
        "legacy_pack",
        "physical_unit",
        "physical_unit",
        "physical_unit",
    ]
    assert current.total_amount == Decimal("36.02")


async def test_unknown_blister_size_stays_explicit_null_and_prevents_blister_sale(
    db_session: AsyncSession,
) -> None:
    sale = await physical_draft(db_session, blister=False)
    item = await put_physical(db_session, sale.id)
    assert item.physical_packaging is not None
    assert item.physical_packaging.model_dump()["units_per_blister"] is None
    with pytest.raises(BusinessRuleError):
        await put_physical(db_session, sale.id, position=2, sale_format="blister")


@pytest.mark.parametrize(
    "field,value", [("units_per_pack", 60), ("units_per_blister", 15), ("dispense_step", 2)]
)
async def test_stale_composition_requires_explicit_new_cart_row(
    db_session: AsyncSession,
    field: str,
    value: int,
) -> None:
    sale = await physical_draft(db_session)
    item = await put_physical(db_session, sale.id, quantity=2)
    request = await physical_request(db_session, sale.id)
    original = await save(db_session, request)
    context = runtime(db_session)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,ARRAY[:field],to_jsonb(CAST(:value AS int))) "
            "WHERE id=:id"
        ),
        {"field": field, "value": value, "id": context.stock.catalog_id},
    )
    assert await save(db_session, request) == original
    with pytest.raises((BusinessRuleError, ConflictError)):
        await save(db_session, request.model_copy(update={"operation_id": uuid4()}))
    with pytest.raises((BusinessRuleError, ConflictError)):
        await put_physical(db_session, sale.id, item_id=item.id, quantity=2)


@pytest.mark.parametrize("physical_first", [True, False])
async def test_cart_modes_cannot_be_mixed_or_flipped(
    db_session: AsyncSession,
    physical_first: bool,
) -> None:
    sale = await physical_draft(db_session)
    if physical_first:
        item = await put_physical(db_session, sale.id)
        with pytest.raises((BusinessRuleError, ConflictError)):
            await put_cart_item(db_session, sale.id, item_id=item.id)
        context = runtime(db_session)
        with pytest.raises((BusinessRuleError, ConflictError)):
            await UnitCartService(UnitCartRepository(db_session)).upsert_item(
                tenant_id=context.stock.tenant_id,
                sale_id=sale.id,
                item_id=uuid4(),
                catalog_id=context.stock.catalog_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                position=2,
                sale_format="unit",
                quantity=1,
            )
    else:
        item_id = await put_cart_item(db_session, sale.id)
        with pytest.raises((BusinessRuleError, ConflictError)):
            await put_physical(db_session, sale.id, item_id=item_id)
        with pytest.raises((BusinessRuleError, ConflictError)):
            await put_physical(db_session, sale.id, position=2)


async def test_old_stock_preparation_never_silently_ignores_physical_cart(
    db_session: AsyncSession,
) -> None:
    sale = await physical_draft(db_session)
    await put_physical(db_session, sale.id)
    request = await physical_request(db_session, sale.id)
    await save(db_session, request)
    context = runtime(db_session)
    with pytest.raises(BusinessRuleError):
        await UnitCheckoutPreparationService(UnitCheckoutRepository(db_session)).prepare(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            request=request,
            today=date.today(),
        )


@pytest.mark.parametrize(
    "change",
    [
        "physical_packaging=NULL",
        "physical_packaging=jsonb_set(physical_packaging,'{units_per_blister}','15')",
        "quantity=0",
        "discount_amount=100",
    ],
)
async def test_database_rejects_forged_physical_cart_updates(
    db_session: AsyncSession,
    change: str,
) -> None:
    sale = await physical_draft(db_session)
    item = await put_physical(db_session, sale.id)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(f"UPDATE pos_unit_cart_item SET {change} WHERE id=:id"),
                {"id": item.id},
            )


@pytest.mark.parametrize(
    "change",
    [
        "jsonb_set(physical_packaging,'{units_per_pack}','60')",
        "physical_packaging || jsonb_build_object('extra',true)",
        "jsonb_set(physical_packaging,'{version}','1')",
    ],
)
async def test_database_rejects_forged_physical_cart_insert(
    db_session: AsyncSession,
    change: str,
) -> None:
    sale = await physical_draft(db_session)
    item = await put_physical(db_session, sale.id)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_unit_cart_item(id,tenant_id,branch_id,sale_id,catalog_id,"
                    "position,sale_format,quantity,discount_amount,physical_packaging) "
                    "SELECT :new,tenant_id,branch_id,sale_id,catalog_id,2,sale_format,quantity,"
                    f"discount_amount,{change} FROM pos_unit_cart_item WHERE id=:id"
                ),
                {"new": uuid4(), "id": item.id},
            )


@pytest.mark.parametrize("path", ["complete", "cash"])
async def test_physical_draft_keeps_legacy_payment_paths_closed(
    db_session: AsyncSession,
    path: str,
) -> None:
    sale = await physical_draft(db_session)
    await put_physical(db_session, sale.id)
    context = runtime(db_session)
    statement = (
        "UPDATE sale SET status='completed' WHERE id=:sale"
        if path == "complete"
        else "INSERT INTO sale_payment(id,tenant_id,sale_id,payment_method,amount,currency) "
        "VALUES(:id,:tenant,:sale,'cash',1,'TJS')"
    )
    with pytest.raises(DBAPIError, match="unit_cart_checkout_not_ready"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(statement),
                {"id": uuid4(), "tenant": context.stock.tenant_id, "sale": sale.id},
            )
