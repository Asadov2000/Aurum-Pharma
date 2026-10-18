"""Mutable legacy drafts keep cumulative FEFO allocations consistent with checkout."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol, TypedDict
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import BusinessRuleError, ConflictError
from app.domains.auth.models import AppUser
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Branch, Register, Tenant
from app.domains.inventory.models import Batch
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.models import Sale, SaleItem
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.main import app


class Scaffold(TypedDict):
    tenant: Tenant
    branch: Branch
    register: Register
    item: TenantCatalog
    batch: Batch
    cashier: AppUser


class ScaffoldFactory(Protocol):
    async def __call__(
        self,
        *,
        batch_qty: Decimal = Decimal("100"),
        sale_price: Decimal = Decimal("10"),
        dispensing_type: str = "otc",
    ) -> Scaffold: ...


@dataclass(frozen=True)
class DraftSource:
    service: POSService
    sale: Sale
    catalog_id: UUID
    first: Batch
    second: Batch
    cashier_id: UUID


async def source(
    session: AsyncSession, factory: ScaffoldFactory, *, dispensing_type: str = "otc"
) -> DraftSource:
    scaffold = await factory(
        batch_qty=Decimal("1"), sale_price=Decimal("10"), dispensing_type=dispensing_type
    )
    inventory = InventoryRepository(session)
    second = await inventory.create_batch(
        tenant_id=scaffold["tenant"].id,
        branch_id=scaffold["branch"].id,
        catalog_id=scaffold["item"].id,
        expires_at=date.today() + timedelta(days=240),
        purchase_price=Decimal("3.00"),
        sale_price=Decimal("20.00"),
        qty_initial=Decimal("5.000"),
        qty_remaining=Decimal("0.000"),
    )
    await inventory.insert_movement(
        tenant_id=scaffold["tenant"].id,
        batch_id=second.id,
        movement_type="incoming",
        qty_delta=Decimal("5.000"),
    )
    await session.refresh(second)
    service = POSService(POSRepository(session))
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0.00"),
    )
    sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    return DraftSource(
        service, sale, scaffold["item"].id, scaffold["batch"], second, scaffold["cashier"].id
    )


async def add(original: DraftSource, quantity: str) -> list[SaleItem]:
    rows, _ = await original.service.add_item(
        sale_id=original.sale.id,
        catalog_id=original.catalog_id,
        qty=Decimal(quantity),
        actor_id=original.cashier_id,
    )
    return rows


async def quantities(original: DraftSource) -> dict[UUID, Decimal]:
    result: dict[UUID, Decimal] = {}
    for row in await original.service.repo.list_items(original.sale.id):
        result[row.batch_id] = result.get(row.batch_id, Decimal("0")) + row.qty
    return result


async def facts(session: AsyncSession, original: DraftSource) -> dict[str, list[dict[str, object]]]:
    queries = {
        "sale": "SELECT to_jsonb(row) FROM sale row WHERE id=:sale",
        "items": "SELECT to_jsonb(row) FROM sale_item row WHERE sale_id=:sale ORDER BY id",
        "batches": "SELECT to_jsonb(row) FROM batch row WHERE id IN (:first,:second) ORDER BY id",
        "movements": (
            "SELECT to_jsonb(row) FROM batch_movement row "
            "WHERE batch_id IN (:first,:second) ORDER BY id"
        ),
    }
    result: dict[str, list[dict[str, object]]] = {}
    values = {"sale": original.sale.id, "first": original.first.id, "second": original.second.id}
    for key, query in queries.items():
        rows = await session.execute(text(query), values)
        result[key] = list(rows.scalars())
    return result


async def checkout_matches_draft(original: DraftSource) -> None:
    rows = await original.service.repo.list_items(original.sale.id)
    before = await quantities(original)
    expected_total = original.sale.total_amount
    quantity = sum((row.qty for row in rows), Decimal("0"))
    result = await original.service.checkout(
        tenant_id=original.sale.tenant_id,
        register_id=original.sale.register_id,
        cashier_user_id=original.cashier_id,
        operation_id=uuid4(),
        draft_sale_id=original.sale.id,
        items=[(original.catalog_id, quantity)],
        payments=[("cash", expected_total, None)],
    )
    actual: dict[UUID, Decimal] = {}
    for row in result.items:
        actual[row.batch_id] = actual.get(row.batch_id, Decimal("0")) + row.qty
    assert actual == before
    assert result.total_amount == expected_total
    await original.service.repo.session.refresh(original.first)
    await original.service.repo.session.refresh(original.second)
    first_spent = before.get(original.first.id, Decimal("0"))
    assert original.first.qty_remaining == Decimal("1") - first_spent
    second_spent = before.get(original.second.id, Decimal("0"))
    assert original.second.qty_remaining == Decimal("5") - second_spent


async def test_repeated_scans_use_remaining_earlier_batch_and_its_actual_prices(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory
) -> None:
    original = await source(db_session, pos_scaffold)
    first = await add(original, "1")
    await add(original, "1")
    assert await quantities(original) == {
        original.first.id: Decimal("1"),
        original.second.id: Decimal("1"),
    }
    rows = await original.service.repo.list_items(original.sale.id)
    assert rows[0].id == first[0].id
    assert [(row.unit_price, row.total_price) for row in rows] == [
        (Decimal("10.00"), Decimal("10.00")),
        (Decimal("20.00"), Decimal("20.00")),
    ]
    assert original.sale.total_amount == Decimal("30.00")
    assert original.first.qty_remaining == Decimal("1")
    assert original.second.qty_remaining == Decimal("5")
    await checkout_matches_draft(original)


async def test_growing_one_row_splits_at_fefo_boundary_and_preserves_target_id(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory
) -> None:
    original = await source(db_session, pos_scaffold)
    first = await add(original, "1")
    updated = await original.service.update_item(
        sale_id=original.sale.id, item_id=first[0].id, qty=Decimal("2")
    )
    assert updated.id == first[0].id
    assert updated.batch_id == original.first.id and updated.qty == Decimal("1")
    assert await quantities(original) == {
        original.first.id: Decimal("1"),
        original.second.id: Decimal("1"),
    }
    assert original.sale.total_amount == Decimal("30.00")
    await checkout_matches_draft(original)


async def test_growing_row_counts_other_draft_rows_against_the_same_stock(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory
) -> None:
    original = await source(db_session, pos_scaffold)
    first = await add(original, "1")
    await add(original, "1")
    existing_ids = {row.id for row in await original.service.repo.list_items(original.sale.id)}
    await original.service.update_item(
        sale_id=original.sale.id, item_id=first[0].id, qty=Decimal("2")
    )
    assert await quantities(original) == {
        original.first.id: Decimal("1"),
        original.second.id: Decimal("2"),
    }
    current_rows = await original.service.repo.list_items(original.sale.id)
    assert existing_ids <= {row.id for row in current_rows}
    assert original.sale.total_amount == Decimal("50.00")
    await checkout_matches_draft(original)


@pytest.mark.parametrize("mutation", ["shrink", "shrink_earlier", "delete"])
async def test_shrink_or_delete_reallocates_remaining_rows_to_the_earliest_batch(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory, mutation: str
) -> None:
    original = await source(db_session, pos_scaffold)
    rows = await add(original, "3")
    earlier, later = rows
    if mutation == "shrink":
        await original.service.update_item(
            sale_id=original.sale.id, item_id=later.id, qty=Decimal("1")
        )
        assert await quantities(original) == {
            original.first.id: Decimal("1"),
            original.second.id: Decimal("1"),
        }
        assert original.sale.total_amount == Decimal("30.00")
    elif mutation == "shrink_earlier":
        await original.service.update_item(
            sale_id=original.sale.id, item_id=earlier.id, qty=Decimal("0.5")
        )
        assert await quantities(original) == {
            original.first.id: Decimal("1"),
            original.second.id: Decimal("1.5"),
        }
        assert original.sale.total_amount == Decimal("40.00")
        remaining = await original.service.repo.list_items(original.sale.id)
        assert {earlier.id, later.id} <= {row.id for row in remaining}
    else:
        await original.service.delete_item(sale_id=original.sale.id, item_id=earlier.id)
        assert await quantities(original) == {
            original.first.id: Decimal("1"),
            original.second.id: Decimal("1"),
        }
        assert original.sale.total_amount == Decimal("30.00")
        remaining = await original.service.repo.list_items(original.sale.id)
        assert later.id in {row.id for row in remaining}
        assert earlier.id not in {row.id for row in remaining}
    await checkout_matches_draft(original)


@pytest.mark.parametrize("mutation", ["add", "update"])
async def test_insufficient_aggregate_stock_preserves_all_draft_and_stock_facts(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory, mutation: str
) -> None:
    original = await source(db_session, pos_scaffold)
    first = await add(original, "1")
    before = await facts(db_session, original)
    with pytest.raises(BusinessRuleError):
        if mutation == "add":
            await add(original, "6")
        else:
            await original.service.update_item(
                sale_id=original.sale.id, item_id=first[0].id, qty=Decimal("7")
            )
    assert await facts(db_session, original) == before


async def test_add_command_retry_after_another_add_does_not_allocate_again(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory
) -> None:
    original = await source(db_session, pos_scaffold)
    operation_id = uuid4()
    saved = await original.service.add_item_command(
        tenant_id=original.sale.tenant_id,
        sale_id=original.sale.id,
        catalog_id=original.catalog_id,
        qty=Decimal("1"),
        expired_sale_confirmed=False,
        actor_id=original.cashier_id,
        operation_id=operation_id,
    )
    await add(original, "1")
    before = await facts(db_session, original)
    retry = await original.service.add_item_command(
        tenant_id=original.sale.tenant_id,
        sale_id=original.sale.id,
        catalog_id=original.catalog_id,
        qty=Decimal("1.000"),
        expired_sale_confirmed=False,
        actor_id=original.cashier_id,
        operation_id=operation_id,
    )
    assert retry == saved
    assert await facts(db_session, original) == before
    with pytest.raises(ConflictError):
        await original.service.add_item_command(
            tenant_id=original.sale.tenant_id,
            sale_id=original.sale.id,
            catalog_id=original.catalog_id,
            qty=Decimal("2"),
            expired_sale_confirmed=False,
            actor_id=original.cashier_id,
            operation_id=operation_id,
        )
    assert await facts(db_session, original) == before
    await checkout_matches_draft(original)


async def test_splitting_discounted_row_preserves_absolute_discount_once(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory
) -> None:
    original = await source(db_session, pos_scaffold)
    first = (await add(original, "1"))[0]
    await original.service.repo.update_item(
        first, discount_amount=Decimal("1.00"), total_price=Decimal("9.00")
    )
    await original.service.repo.update_sale(original.sale, total_amount=Decimal("9.00"))
    await original.service.update_item(
        sale_id=original.sale.id, item_id=first.id, qty=Decimal("2")
    )
    rows = await original.service.repo.list_items(original.sale.id)
    assert sum((row.discount_amount for row in rows), Decimal("0")) == Decimal("1.00")
    assert sum((row.total_price for row in rows), Decimal("0")) == Decimal("29.00")
    assert original.sale.total_amount == Decimal("29.00")
    await original.service.add_payment(
        sale_id=original.sale.id, payment_method="cash", amount=Decimal("29.00")
    )
    completed = await original.service.complete(sale_id=original.sale.id)
    assert completed.status == "completed" and completed.total_amount == Decimal("29.00")


@asynccontextmanager
async def api_actor(session: AsyncSession, original: DraftSource) -> AsyncIterator[None]:
    actor = CurrentUser(
        user_id=original.cashier_id,
        tenant_id=original.sale.tenant_id,
        is_developer=False,
        is_administrator=False,
        permissions={"pos.sell", "sales.view.own"},
        permission_scopes={
            "pos.sell": frozenset({original.sale.branch_id}),
            "sales.view.own": frozenset({original.sale.branch_id}),
        },
    )

    async def override_db() -> AsyncIterator[AsyncSession]:
        yield session

    async def override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = override_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)


async def test_http_repeated_add_and_quantity_change_refetch_authoritative_fefo_rows(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory, client: AsyncClient
) -> None:
    original = await source(db_session, pos_scaffold)
    path = f"/api/v1/sales/{original.sale.id}"
    async with api_actor(db_session, original):
        first = await client.post(
            f"{path}/items",
            json={
                "operation_id": str(uuid4()),
                "catalog_id": str(original.catalog_id),
                "qty": "1",
            },
        )
        assert first.status_code == 201, first.text
        second = await client.post(
            f"{path}/items",
            json={
                "operation_id": str(uuid4()),
                "catalog_id": str(original.catalog_id),
                "qty": "1",
            },
        )
        assert second.status_code == 201, second.text
        current = await client.get(path)
        assert current.status_code == 200, current.text
        assert Decimal(current.json()["total_amount"]) == Decimal("30.00")
        target_id = first.json()["items"][0]["id"]
        changed = await client.patch(
            f"{path}/items/{target_id}",
            json={"operation_id": str(uuid4()), "qty": "2"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["id"] == target_id
        current = await client.get(path)
        assert current.status_code == 200, current.text
        assert Decimal(current.json()["total_amount"]) == Decimal("50.00")
        assert sum((Decimal(row["qty"]) for row in current.json()["items"]), Decimal("0")) == 3
        assert sum(
            (
                Decimal(row["qty"])
                for row in current.json()["items"]
                if row["batch_id"] == str(original.first.id)
            ),
            Decimal("0"),
        ) == 1
    await checkout_matches_draft(original)


async def test_reallocation_does_not_change_other_catalog_positions(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory
) -> None:
    original = await source(db_session, pos_scaffold)
    other = await CatalogService(CatalogRepository(db_session)).create_item(
        tenant_id=original.sale.tenant_id,
        fields={"brand_name": "Unrelated item", "dispensing_type": "otc"},
    )
    inventory = InventoryRepository(db_session)
    batch = await inventory.create_batch(
        tenant_id=original.sale.tenant_id,
        branch_id=original.sale.branch_id,
        catalog_id=other.id,
        expires_at=date.today() + timedelta(days=200),
        purchase_price=Decimal("3.00"),
        sale_price=Decimal("7.00"),
        qty_initial=Decimal("5"),
        qty_remaining=Decimal("0"),
    )
    await inventory.insert_movement(
        tenant_id=original.sale.tenant_id,
        batch_id=batch.id,
        movement_type="incoming",
        qty_delta=Decimal("5"),
    )
    other_rows, _ = await original.service.add_item(
        sale_id=original.sale.id, catalog_id=other.id, qty=Decimal("1")
    )
    other_id = other_rows[0].id
    before = await db_session.scalar(
        text("SELECT to_jsonb(row) FROM sale_item row WHERE id=:id"), {"id": other_id}
    )
    first = await add(original, "1")
    await original.service.update_item(
        sale_id=original.sale.id, item_id=first[0].id, qty=Decimal("2")
    )
    await original.service.delete_item(sale_id=original.sale.id, item_id=first[0].id)
    after = await db_session.scalar(
        text("SELECT to_jsonb(row) FROM sale_item row WHERE id=:id"), {"id": other_id}
    )
    assert after == before


async def test_fractional_repeated_scans_match_exact_money_and_checkout(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory
) -> None:
    original = await source(db_session, pos_scaffold)
    await add(original, "0.75")
    await add(original, "0.75")
    assert await quantities(original) == {
        original.first.id: Decimal("1"),
        original.second.id: Decimal("0.5"),
    }
    assert original.sale.total_amount == Decimal("20.00")
    await checkout_matches_draft(original)


async def test_split_preserves_line_scoped_prescription_with_only_doctor_license(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory
) -> None:
    original = await source(db_session, pos_scaffold, dispensing_type="prescription")
    first = (await add(original, "1"))[0]
    prescription = await original.service.add_prescription(
        sale_id=original.sale.id,
        fields={"sale_item_id": first.id, "doctor_license": "SYNTHETIC-LICENSE"},
        actor_id=original.cashier_id,
    )
    await original.service.update_item(
        sale_id=original.sale.id, item_id=first.id, qty=Decimal("2")
    )
    rows = await original.service.repo.list_items(original.sale.id)
    logs = await original.service.repo.list_prescriptions(original.sale.id)
    assert len(rows) == len(logs) == 2
    assert {log.sale_item_id for log in logs} == {row.id for row in rows}
    assert prescription.id in {log.id for log in logs}
    assert all(log.doctor_license == "SYNTHETIC-LICENSE" for log in logs)
    await original.service.add_payment(
        sale_id=original.sale.id, payment_method="cash", amount=Decimal("30.00")
    )
    completed = await original.service.complete(sale_id=original.sale.id)
    assert completed.status == "completed"
    assert completed.total_amount == Decimal("30.00")


async def assert_stale_draft_cannot_complete(
    session: AsyncSession, original: DraftSource
) -> None:
    async with session.begin_nested() as savepoint:
        await original.service.add_payment(
            sale_id=original.sale.id,
            payment_method="cash",
            amount=original.sale.total_amount,
        )
        before = await facts(session, original)
        with pytest.raises(BusinessRuleError) as error:
            await original.service.complete(sale_id=original.sale.id)
        assert error.value.details["reason"] == "insufficient_batch_stock"
        assert await facts(session, original) == before
        await savepoint.rollback()
    await session.refresh(original.sale)
    await session.refresh(original.first)
    await session.refresh(original.second)


@pytest.mark.parametrize("mutation", ["delete", "decrease"])
async def test_shortage_allows_progressive_reduction_until_fefo_fits(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory, mutation: str
) -> None:
    original = await source(db_session, pos_scaffold)
    for _ in range(6):
        await add(original, "1")
    rows = await original.service.repo.list_items(original.sale.id)
    target = rows[1]
    await original.service.repo.update_item(
        target, discount_amount=Decimal("1.00"), total_price=Decimal("19.00")
    )
    await original.service.repo.update_sale(original.sale, total_amount=Decimal("109.00"))
    inventory = InventoryRepository(db_session)
    for batch in (original.first, original.second):
        await inventory.insert_movement(
            tenant_id=original.sale.tenant_id,
            batch_id=batch.id,
            movement_type="write_off",
            qty_delta=Decimal("-1"),
        )
    before = await facts(db_session, original)
    untouched = [row for row in before["items"] if row["id"] != str(target.id)]
    if mutation == "delete":
        await original.service.delete_item(sale_id=original.sale.id, item_id=target.id)
        assert original.sale.total_amount == Decimal("90.00")
    else:
        await original.service.update_item(
            sale_id=original.sale.id, item_id=target.id, qty=Decimal("0.5")
        )
        assert target.batch_id == original.second.id
        assert target.unit_price == Decimal("20.00")
        assert target.discount_amount == Decimal("1.00")
        assert target.total_price == Decimal("9.00")
        assert original.sale.total_amount == Decimal("99.00")
    after = await facts(db_session, original)
    assert [row for row in after["items"] if row["id"] != str(target.id)] == untouched
    assert after["batches"] == before["batches"]
    assert after["movements"] == before["movements"]
    if mutation == "decrease":
        with pytest.raises(BusinessRuleError):
            await original.service.update_item(
                sale_id=original.sale.id, item_id=target.id, qty=Decimal("0.01")
            )
        assert await facts(db_session, original) == after
    with pytest.raises(BusinessRuleError):
        await add(original, "1")
    with pytest.raises(BusinessRuleError):
        await original.service.update_item(
            sale_id=original.sale.id, item_id=rows[-1].id, qty=Decimal("2")
        )
    assert await facts(db_session, original) == after
    await assert_stale_draft_cannot_complete(db_session, original)
    remaining = await original.service.repo.list_items(original.sale.id)
    while sum((row.qty for row in remaining), Decimal("0")) > 4:
        await original.service.delete_item(sale_id=original.sale.id, item_id=remaining[0].id)
        remaining = await original.service.repo.list_items(original.sale.id)
    assert await quantities(original) == {original.second.id: Decimal("4")}
    await original.service.add_payment(
        sale_id=original.sale.id, payment_method="cash", amount=Decimal("80.00")
    )
    completed = await original.service.complete(sale_id=original.sale.id)
    assert completed.status == "completed" and completed.total_amount == Decimal("80.00")
    await db_session.refresh(original.second)
    assert original.second.qty_remaining == 0


async def test_zero_stock_draft_can_be_emptied_one_row_at_a_time(
    db_session: AsyncSession, pos_scaffold: ScaffoldFactory
) -> None:
    original = await source(db_session, pos_scaffold)
    await add(original, "1")
    await add(original, "1")
    inventory = InventoryRepository(db_session)
    for batch, quantity in ((original.first, "1"), (original.second, "5")):
        await inventory.insert_movement(
            tenant_id=original.sale.tenant_id,
            batch_id=batch.id,
            movement_type="write_off",
            qty_delta=-Decimal(quantity),
        )
    before = await facts(db_session, original)
    for row in await original.service.repo.list_items(original.sale.id):
        await original.service.delete_item(sale_id=original.sale.id, item_id=row.id)
    assert await original.service.repo.list_items(original.sale.id) == []
    assert original.sale.total_amount == 0
    after = await facts(db_session, original)
    assert after["batches"] == before["batches"]
    assert after["movements"] == before["movements"]
