"""Product ranks keep immutable units, financial returns and confirmed zero intakes distinct."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.catalog.unit_sales import DispenseUnit
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.physical_cash_refund_review import physical_refund_revision
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_zero_return_repository import PhysicalZeroReturnRepository
from app.domains.pos.physical_zero_return_service import PhysicalZeroReturnService
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import TopProductsOverview
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor
from tests.domains.pos.test_mixed_cash_refund import refund as refund_mixed
from tests.domains.pos.test_mixed_return_journal import mixed_source
from tests.domains.pos.test_mixed_zero_return import facts as mixed_facts
from tests.domains.pos.test_mixed_zero_return import intake as mixed_zero_intake
from tests.domains.pos.test_mixed_zero_return import source as mixed_zero_source
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_refund import refund as refund_physical
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_physical_return_journal import receive as physical_journal_intake
from tests.domains.pos.test_physical_zero_return import all_facts as physical_facts
from tests.domains.pos.test_physical_zero_return import source as physical_zero_source
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime
from tests.domains.pos.test_unit_sale_report_discounts import report_actor, report_day

db_session = receiving_session_fixture
QuantityBasis = Literal["legacy_pack"] | DispenseUnit


async def top(
    session: AsyncSession,
    *,
    basis: QuantityBasis = "legacy_pack",
    sort: Literal["revenue", "quantity"] = "revenue",
    day: date | None = None,
    limit: int = 20,
) -> TopProductsOverview:
    context = runtime(session)
    selected = day or await report_day(session)
    return await POSService(POSRepository(session)).get_top_products_overview(
        tenant_id=context.stock.tenant_id,
        date_from=selected,
        date_to=selected,
        branch_id=context.stock.branch_id,
        sort_by=sort,
        quantity_basis=basis,
        limit=limit,
    )


async def validate_constraints(session: AsyncSession) -> None:
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


async def test_physical_formats_use_original_base_units_and_count_one_receipt(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id, position=1, quantity=2)
    await put_physical(db_session, source.sale.id, position=2, sale_format="blister", quantity=1)
    pack = await put_physical(
        db_session, source.sale.id, position=3, sale_format="pack", quantity=1
    )
    posted = await complete(db_session, await saved_request(db_session, source.sale))
    await validate_constraints(db_session)
    report = await top(db_session)
    row = report.rows[0]
    assert row.catalog_id == runtime(db_session).stock.catalog_id
    assert row.quantity == Decimal("0")
    assert [(part.unit, part.quantity) for part in row.physical_quantities] == [
        ("tablet", Decimal("42"))
    ]
    assert row.revenue == posted.total_amount and row.receipts_count == 1
    refund = await refund_physical(db_session, posted.sale_id, pack.id, posted.shift_id, quantity=1)
    await validate_constraints(db_session)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation="
            "jsonb_set(jsonb_set(unit_sale_preparation,'{unit}','\"capsule\"'),"
            "'{units_per_pack}','100') WHERE id=:id"
        ),
        {"id": row.catalog_id},
    )
    before = await physical_facts(db_session, posted.tenant_id)
    ranked = await top(db_session, sort="quantity", basis="tablet")
    assert ranked.quantity_basis == "tablet"
    assert ranked.rows[0].physical_quantities[0].quantity == Decimal("41")
    assert ranked.rows[0].revenue == posted.total_amount - refund.total_amount
    assert ranked.rows[0].receipts_count == 1
    assert (await top(db_session, sort="quantity", basis="legacy_pack")).rows == []
    assert (await top(db_session, sort="quantity", basis="capsule")).rows == []
    assert await physical_facts(db_session, posted.tenant_id) == before


async def test_mixed_legacy_is_counted_once_and_full_refund_removes_the_row(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    await validate_constraints(db_session)
    row = (await top(db_session)).rows[0]
    assert row.quantity == Decimal("0.003")
    assert [(part.unit, part.quantity) for part in row.physical_quantities] == [
        ("tablet", Decimal("60"))
    ]
    assert row.revenue == source.original.total_amount and row.receipts_count == 1
    first = await refund_mixed(db_session, source)
    await validate_constraints(db_session)
    row = (await top(db_session)).rows[0]
    assert row.quantity == Decimal("0.002")
    assert row.physical_quantities[0].quantity == Decimal("59")
    assert row.revenue == source.original.total_amount - first.total_amount
    assert row.receipts_count == 1
    await refund_mixed(db_session, source, source.selections("0.002", 59))
    await validate_constraints(db_session)
    assert (await top(db_session)).rows == []
    assert (await top(db_session, sort="quantity", basis="tablet")).rows == []


@pytest.mark.parametrize("kind", ["physical", "mixed"])
async def test_only_confirmed_zero_intake_changes_quantity_without_changing_revenue(
    db_session: AsyncSession, kind: Literal["physical", "mixed"]
) -> None:
    if kind == "mixed":
        mixed = await mixed_zero_source(db_session)
        original = mixed.original
        line_id = mixed.physical_id
        initial_units = Decimal("60")
    else:
        original = await physical_zero_source(db_session)
        line_id = original.lines[0].id
        initial_units = Decimal("30")
    await validate_constraints(db_session)
    initial = (await top(db_session)).rows[0]
    if kind == "physical":
        # A prepared journal is not a confirmed return event in a financial report.
        await physical_journal_intake(db_session, original.sale_id, line_id)
        await validate_constraints(db_session)
        assert (await top(db_session)).rows[0] == initial
    selections = (PhysicalReturnSelection(line_id, "unit", 1),)
    if kind == "mixed":
        result = await mixed_zero_intake(db_session, mixed, selections=selections)
    else:
        cursors = await PhysicalReturnJournalRepository(db_session).cursors(
            original.tenant_id, original.stock_command_id
        )
        result = await PhysicalZeroReturnService(PhysicalZeroReturnRepository(db_session)).receive(
            tenant_id=original.tenant_id,
            actor_id=original.cashier_id,
            parent_sale_id=original.sale_id,
            operation_id=uuid4(),
            selections=selections,
            expected_revision_hash=physical_refund_revision(original, cursors, selections),
        )
    await validate_constraints(db_session)
    assert result.total_amount == Decimal("0.00")
    after = (await top(db_session)).rows[0]
    assert after.physical_quantities[0].quantity == initial_units - 1
    assert after.quantity == initial.quantity
    assert after.revenue == initial.revenue and after.receipts_count == 1
    if kind == "mixed":
        await refund_mixed(db_session, mixed, mixed.selections("0.003", 59))
        await validate_constraints(db_session)
        assert (await top(db_session)).rows == []


async def test_report_only_http_actor_has_scoped_cost_free_read_without_source_writes(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    reader = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("reports.view",)
    )
    source = await mixed_source(db_session, fractional_legacy=True)
    await validate_constraints(db_session)
    before = await mixed_facts(db_session)
    day = await report_day(db_session)
    params = {
        "from": str(day),
        "to": str(day),
        "branch_id": str(context.stock.branch_id),
        "sort_by": "quantity",
        "quantity_basis": "tablet",
        "limit": "5",
    }
    async with report_actor(db_session, reader) as actor:
        actor.permissions = {"reports.view"}
        actor.permission_scopes = {"reports.view": frozenset({context.stock.branch_id})}
        for table in ("pos_mixed_checkout", "unit_stock_cart_command_v2"):
            assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        response = await client.get("/api/v1/reports/top-products", params=params)
        assert response.status_code == 200
        payload = response.json()
        assert payload["quantity_basis"] == "tablet"
        assert len(payload["rows"]) == 1
        row = payload["rows"][0]
        assert Decimal(row["quantity"]) == Decimal("0.003")
        assert row["physical_quantities"] == [{"unit": "tablet", "quantity": "60"}]
        assert Decimal(row["revenue"]) == source.original.total_amount
        assert_no_cost_fields(payload)
        actor.permission_scopes = {"reports.view": frozenset({uuid4()})}
        assert (await client.get("/api/v1/reports/top-products", params=params)).status_code == 403
    assert await mixed_facts(db_session) == before


async def legacy_sale(
    session: AsyncSession, register_id: UUID, name: str, quantity: Decimal
) -> UUID:
    context = runtime(session)
    product = await CatalogService(CatalogRepository(session)).create_item(
        tenant_id=context.stock.tenant_id, fields={"brand_name": name, "base_price": Decimal("10")}
    )
    inventory = InventoryRepository(session)
    batch = await inventory.create_batch(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        catalog_id=product.id,
        expires_at=date.today() + timedelta(days=180),
        purchase_price=Decimal("3"),
        sale_price=Decimal("10"),
        qty_initial=Decimal("5"),
        qty_remaining=Decimal("0"),
    )
    await inventory.insert_movement(
        tenant_id=context.stock.tenant_id,
        batch_id=batch.id,
        movement_type="incoming",
        qty_delta=Decimal("5"),
    )
    await session.refresh(batch)
    assert batch.qty_remaining == Decimal("5")
    pos = POSService(POSRepository(session))
    sale = await pos.create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=register_id,
        cashier_user_id=context.actor_id,
    )
    await pos.add_item(sale_id=sale.id, catalog_id=product.id, qty=quantity)
    await pos.add_payment(sale_id=sale.id, payment_method="cash", amount=sale.total_amount)
    await pos.complete(sale_id=sale.id)
    return product.id


async def test_quantity_basis_filters_before_limit_and_ties_keep_stable_product_order(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id, sale_format="pack", quantity=1)
    posted = await complete(db_session, await saved_request(db_session, source.sale))
    a = await legacy_sale(db_session, posted.register_id, "A ranking", Decimal("2"))
    b = await legacy_sale(db_session, posted.register_id, "B ranking", Decimal("2"))
    await validate_constraints(db_session)
    ranked = await top(db_session, sort="quantity", limit=1)
    assert ranked.quantity_basis == "legacy_pack"
    assert [row.catalog_id for row in ranked.rows] == [a]
    all_legacy = await top(db_session, sort="quantity")
    assert [row.catalog_id for row in all_legacy.rows] == [a, b]
    assert all(row.quantity == Decimal("2") for row in all_legacy.rows)
    assert all(row.physical_quantities == [] for row in all_legacy.rows)
    physical = await top(db_session, basis="tablet", sort="quantity")
    assert [row.catalog_id for row in physical.rows] == [runtime(db_session).stock.catalog_id]


async def test_training_physical_checkout_is_rejected_and_draft_is_excluded(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(
        db_session, source.sale.id, sale_format="pack", quantity=1, discount=Decimal("59.99")
    )
    assert (await top(db_session)).rows == []
    await POSRepository(db_session).update_sale(source.sale, is_test=True)
    request = await saved_request(db_session, source.sale)
    tenant_id = runtime(db_session).stock.tenant_id
    before = await physical_facts(db_session, tenant_id)
    with pytest.raises(BusinessRuleError, match="Physical test sales"):
        await complete(db_session, request)
    assert await physical_facts(db_session, tenant_id) == before
    await validate_constraints(db_session)
    assert (await top(db_session)).rows == []
    assert (await top(db_session, sort="quantity", basis="tablet")).rows == []


async def test_refund_on_next_day_and_shift_is_negative_only_in_its_own_period(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await checkout_source(db_session)
    context = runtime(db_session)
    pos = POSService(POSRepository(db_session))
    previous = datetime.now(UTC) - timedelta(days=1)
    await pos.repo.update_sale(source.sale, created_at=previous - timedelta(minutes=1))
    shift = await pos.repo.get_shift(source.sale.shift_id)
    assert shift is not None
    await pos.repo.update_shift(shift, opened_at=previous - timedelta(minutes=2))
    item = await put_physical(db_session, source.sale.id, sale_format="pack", quantity=1)
    with monkeypatch.context() as clock:
        clock.setattr("app.domains.pos.physical_cash_checkout_service.utc_now", lambda: previous)
        original = await complete(db_session, await saved_request(db_session, source.sale))
    await validate_constraints(db_session)
    await pos.close_shift(
        shift_id=original.shift_id,
        closing_cash_actual=original.total_amount,
        closed_by_user_id=context.actor_id,
    )
    new_shift = await pos.open_shift(
        tenant_id=context.stock.tenant_id,
        register_id=original.register_id,
        opened_by_user_id=context.actor_id,
        opening_cash=original.total_amount,
    )
    refund = await refund_physical(db_session, original.sale_id, item.id, new_shift.id, quantity=10)
    await validate_constraints(db_session)
    old_day = await db_session.scalar(
        text("SELECT (CAST(:instant AS timestamptz) AT TIME ZONE 'Asia/Dushanbe')::date"),
        {"instant": previous},
    )
    assert isinstance(old_day, date)
    old = (await top(db_session, day=old_day)).rows[0]
    new = (await top(db_session)).rows[0]
    assert old.physical_quantities[0].quantity == Decimal("30")
    assert old.revenue == original.total_amount and old.receipts_count == 1
    assert new.physical_quantities[0].quantity == Decimal("-10")
    assert new.revenue == -refund.total_amount and new.receipts_count == 0


async def test_zero_marker_uses_its_own_day_instead_of_original_sale_day(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await checkout_source(db_session)
    context = runtime(db_session)
    previous = datetime.now(UTC) - timedelta(days=1)
    await POSRepository(db_session).update_sale(
        source.sale, created_at=previous - timedelta(minutes=1)
    )
    shift = await POSRepository(db_session).get_shift(source.sale.shift_id)
    assert shift is not None
    await POSRepository(db_session).update_shift(shift, opened_at=previous - timedelta(minutes=2))
    await put_physical(
        db_session, source.sale.id, sale_format="pack", quantity=1, discount=Decimal("59.99")
    )
    with monkeypatch.context() as clock:
        clock.setattr("app.domains.pos.physical_cash_checkout_service.utc_now", lambda: previous)
        original = await complete(db_session, await saved_request(db_session, source.sale))
    await validate_constraints(db_session)
    selections = (PhysicalReturnSelection(original.lines[0].id, "unit", 1),)
    await PhysicalZeroReturnService(PhysicalZeroReturnRepository(db_session)).receive(
        tenant_id=original.tenant_id,
        actor_id=context.actor_id,
        parent_sale_id=original.sale_id,
        operation_id=uuid4(),
        selections=selections,
        expected_revision_hash=physical_refund_revision(original, {}, selections),
    )
    await validate_constraints(db_session)
    old_day = await db_session.scalar(
        text("SELECT (CAST(:instant AS timestamptz) AT TIME ZONE 'Asia/Dushanbe')::date"),
        {"instant": previous},
    )
    assert isinstance(old_day, date)
    old = (await top(db_session, day=old_day)).rows[0]
    new = (await top(db_session)).rows[0]
    assert old.physical_quantities[0].quantity == Decimal("30")
    assert old.revenue == original.total_amount and old.receipts_count == 1
    assert new.physical_quantities[0].quantity == Decimal("-1")
    assert new.revenue == Decimal("0") and new.receipts_count == 0
