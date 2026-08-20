"""Sales flow: draft → FEFO → payments → complete; immutability after."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.audit.models import AuditLog
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def _open_shift(service: POSService, s) -> None:
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )


async def test_create_draft_sale(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)

    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    assert sale.status == "draft"
    assert sale.is_test is False  # tenant is 'active'
    assert sale.total_amount == Decimal("0.00")


async def test_add_item_uses_fefo(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )

    created, requires_rx = await service.add_item(
        sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("2")
    )
    assert len(created) == 1
    assert created[0].batch_id == s["batch"].id
    assert created[0].unit_price == Decimal("10.00")
    assert created[0].total_price == Decimal("20.00")
    assert requires_rx is False


async def test_add_item_rejects_zero_sale_price(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(sale_price=Decimal("0"))
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )

    with pytest.raises(BusinessRuleError, match="no valid sale price"):
        await service.add_item(
            sale_id=sale.id,
            catalog_id=s["item"].id,
            qty=Decimal("1"),
        )


async def test_add_item_rejects_inactive_catalog_item(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    s = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    s["item"].is_active = False
    await db_session.flush()

    with pytest.raises(BusinessRuleError, match="Inactive catalog item cannot be sold"):
        await service.add_item(
            sale_id=sale.id,
            catalog_id=s["item"].id,
            qty=Decimal("1"),
        )


async def test_add_item_hides_deleted_catalog_item(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    s = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    s["item"].deleted_at = datetime.now(UTC)
    await db_session.flush()

    with pytest.raises(NotFoundError, match="Catalog item not found"):
        await service.add_item(
            sale_id=sale.id,
            catalog_id=s["item"].id,
            qty=Decimal("1"),
        )


async def test_add_item_splits_across_batches(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(batch_qty=3, sale_price=10)
    inv_repo = InventoryRepository(db_session)
    # second batch with later expiry
    second = await inv_repo.create_batch(
        tenant_id=s["tenant"].id,
        branch_id=s["branch"].id,
        catalog_id=s["item"].id,
        expires_at=date.today() + timedelta(days=240),
        purchase_price=Decimal("3"),
        sale_price=Decimal("10"),
        qty_initial=Decimal("5"),
        qty_remaining=Decimal("0"),
    )
    await inv_repo.insert_movement(
        tenant_id=s["tenant"].id,
        batch_id=second.id,
        movement_type="incoming",
        qty_delta=Decimal("5"),
    )
    # Trigger updated qty_remaining in the DB; refresh ORM identity.
    await db_session.refresh(second)

    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )

    created, _ = await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("5"))
    # FEFO should drain the earlier-expiring batch (qty=3) first, then take 2
    # from the second.
    assert len(created) == 2
    assert created[0].qty == Decimal("3.000")
    assert created[1].qty == Decimal("2.000")


async def test_complete_decreases_batch_qty(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=100)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("4"))
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("40.00"))

    completed = await service.complete(sale_id=sale.id)
    assert completed.status == "completed"
    assert completed.receipt_number == "000001"
    assert completed.completed_at is not None

    batch = await InventoryRepository(db_session).get_batch(s["batch"].id)
    assert batch is not None
    assert batch.qty_remaining == Decimal("96.000")


async def test_complete_rechecks_catalog_is_active(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=5)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("1"))
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("10"))
    s["item"].is_active = False
    await db_session.flush()

    with pytest.raises(BusinessRuleError, match="Inactive catalog item cannot be sold"):
        await service.complete(sale_id=sale.id)


async def test_complete_with_insufficient_payment_fails(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(sale_price=10)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("2"))
    await service.add_payment(
        sale_id=sale.id, payment_method="cash", amount=Decimal("10.00")
    )  # only 10, need 20

    with pytest.raises(BusinessRuleError):
        await service.complete(sale_id=sale.id)


async def test_cannot_modify_completed_sale(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(sale_price=5)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    items, _ = await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("1"))
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("5.00"))
    await service.complete(sale_id=sale.id)

    with pytest.raises(ConflictError):
        await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("1"))
    with pytest.raises(ConflictError):
        await service.update_item(sale_id=sale.id, item_id=items[0].id, qty=Decimal("2"))
    with pytest.raises(ConflictError):
        await service.delete_item(sale_id=sale.id, item_id=items[0].id)


async def test_prescription_required_for_rx_items(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(dispensing_type="prescription", sale_price=20)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    _, requires_rx = await service.add_item(
        sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("1")
    )
    assert requires_rx is True
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("20.00"))

    # Without prescription_log — complete must fail
    with pytest.raises(BusinessRuleError):
        await service.complete(sale_id=sale.id)

    # After logging it, complete passes
    prescription = await service.add_prescription(
        sale_id=sale.id,
        fields={
            "prescription_number": "RX-001",
            "doctor_name": "Dr Who",
            "doctor_license": "LIC-001",
            "patient_name": "Patient Name",
            "notes": "Sensitive patient note",
        },
        actor_id=s["cashier"].id,
    )
    audit_entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.table_name == "prescription_log",
                AuditLog.record_id == prescription.id,
                AuditLog.action == "INSERT",
            )
        )
    ).scalar_one()
    assert audit_entry.new_values is not None
    for field in (
        "prescription_number",
        "doctor_name",
        "doctor_license",
        "patient_name",
        "notes",
    ):
        assert audit_entry.new_values[field] == "***"
    assert audit_entry.new_values["sale_id"] == str(sale.id)

    completed = await service.complete(sale_id=sale.id)
    assert completed.status == "completed"


async def test_special_dispensing_is_blocked_until_regulatory_approval(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(dispensing_type="special", sale_price=20)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )

    with pytest.raises(BusinessRuleError) as exc_info:
        await service.add_item(
            sale_id=sale.id,
            catalog_id=s["item"].id,
            qty=Decimal("1"),
        )

    assert exc_info.value.details == {"reason": "special_dispensing_regulatory_approval_required"}
    assert await POSRepository(db_session).list_items(sale.id) == []


async def test_completion_rechecks_special_dispensing_for_existing_draft(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(dispensing_type="otc", sale_price=20)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(
        sale_id=sale.id,
        catalog_id=s["item"].id,
        qty=Decimal("1"),
    )
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("20.00"),
    )
    s["item"].dispensing_type = "special"
    await db_session.flush()

    with pytest.raises(BusinessRuleError) as exc_info:
        await service.complete(sale_id=sale.id)

    assert exc_info.value.details == {"reason": "special_dispensing_regulatory_approval_required"}
    await db_session.refresh(sale)
    assert sale.status == "draft"
    batch = await InventoryRepository(db_session).get_batch(s["batch"].id)
    assert batch is not None
    assert batch.qty_remaining == Decimal("100.000")


async def test_prescription_log_rejects_empty_and_unrelated_details(
    db_session: AsyncSession, pos_scaffold
) -> None:
    rx = await pos_scaffold(dispensing_type="prescription", sale_price=20)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, rx)
    sale = await service.create_sale(
        tenant_id=rx["tenant"].id,
        register_id=rx["register"].id,
        cashier_user_id=rx["cashier"].id,
    )
    await service.add_item(
        sale_id=sale.id,
        catalog_id=rx["item"].id,
        qty=Decimal("1"),
    )

    with pytest.raises(BusinessRuleError, match="At least one prescription detail"):
        await service.add_prescription(
            sale_id=sale.id,
            fields={"notes": "   "},
            actor_id=rx["cashier"].id,
        )
    with pytest.raises(NotFoundError, match="not found in this sale"):
        await service.add_prescription(
            sale_id=sale.id,
            fields={"sale_item_id": rx["item"].id, "prescription_number": "RX-1"},
            actor_id=rx["cashier"].id,
        )

    otc = await pos_scaffold(dispensing_type="otc", sale_price=10)
    await _open_shift(service, otc)
    otc_sale = await service.create_sale(
        tenant_id=otc["tenant"].id,
        register_id=otc["register"].id,
        cashier_user_id=otc["cashier"].id,
    )
    await service.add_item(
        sale_id=otc_sale.id,
        catalog_id=otc["item"].id,
        qty=Decimal("1"),
    )
    with pytest.raises(BusinessRuleError, match="not applicable"):
        await service.add_prescription(
            sale_id=otc_sale.id,
            fields={"prescription_number": "RX-NOT-NEEDED"},
            actor_id=otc["cashier"].id,
        )


async def test_test_sale_flag_for_setup_phase(db_session: AsyncSession, pos_scaffold) -> None:
    """Tenant in `setup` produces is_test sales; stock is NOT decreased."""
    s = await pos_scaffold(tenant_status="setup", sale_price=10, batch_qty=20)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    assert sale.is_test is True

    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("3"))
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("30.00"))
    await service.complete(sale_id=sale.id)

    # qty_remaining unchanged — test sales don't decrement stock.
    batch = await InventoryRepository(db_session).get_batch(s["batch"].id)
    assert batch is not None
    assert batch.qty_remaining == Decimal("20.000")


async def test_complete_with_insufficient_stock_after_lock(
    db_session: AsyncSession, pos_scaffold
) -> None:
    """Simulates a concurrent decrement between add_item (FEFO already
    chose the batch) and complete. lock_batch should re-check qty_remaining
    and surface a BusinessRuleError."""
    s = await pos_scaffold(sale_price=10, batch_qty=5)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("4"))
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("40.00"))

    # Concurrent depletion: someone else writes off the rest of the batch.
    inv_repo = InventoryRepository(db_session)
    await inv_repo.insert_movement(
        tenant_id=s["tenant"].id,
        batch_id=s["batch"].id,
        movement_type="write_off",
        qty_delta=Decimal("-2"),
    )

    # Now the batch has 3 left but the sale needs 4 — complete refuses.
    with pytest.raises(BusinessRuleError):
        await service.complete(sale_id=sale.id)


async def test_complete_rechecks_batch_block_and_expiry(
    db_session: AsyncSession, pos_scaffold
) -> None:
    blocked = await pos_scaffold(sale_price=10, batch_qty=5)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, blocked)
    blocked_sale = await service.create_sale(
        tenant_id=blocked["tenant"].id,
        register_id=blocked["register"].id,
        cashier_user_id=blocked["cashier"].id,
    )
    await service.add_item(
        sale_id=blocked_sale.id,
        catalog_id=blocked["item"].id,
        qty=Decimal("1"),
    )
    await service.add_payment(
        sale_id=blocked_sale.id,
        payment_method="cash",
        amount=Decimal("10"),
    )
    blocked["batch"].is_blocked = True
    await db_session.flush()
    with pytest.raises(BusinessRuleError, match="blocked at checkout"):
        await service.complete(sale_id=blocked_sale.id)

    expired = await pos_scaffold(sale_price=10, batch_qty=5)
    await _open_shift(service, expired)
    expired_sale = await service.create_sale(
        tenant_id=expired["tenant"].id,
        register_id=expired["register"].id,
        cashier_user_id=expired["cashier"].id,
    )
    await service.add_item(
        sale_id=expired_sale.id,
        catalog_id=expired["item"].id,
        qty=Decimal("1"),
    )
    await service.add_payment(
        sale_id=expired_sale.id,
        payment_method="cash",
        amount=Decimal("10"),
    )
    expired["batch"].expires_at = date.today()
    await db_session.flush()
    with pytest.raises(BusinessRuleError, match="Expired batch"):
        await service.complete(sale_id=expired_sale.id)


async def test_expired_sale_confirmation_cannot_bypass_add_item_guard(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=5)
    s["batch"].expires_at = date(2000, 1, 1)
    await db_session.flush()

    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )

    with pytest.raises(BusinessRuleError, match="Insufficient stock"):
        await service.add_item(
            sale_id=sale.id,
            catalog_id=s["item"].id,
            qty=Decimal("1"),
            expired_sale_confirmed=True,
        )


async def test_expired_sale_confirmation_cannot_bypass_completion_guard(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=5)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(
        sale_id=sale.id,
        catalog_id=s["item"].id,
        qty=Decimal("1"),
    )
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("10"),
    )
    s["batch"].expires_at = date(2000, 1, 1)
    await db_session.flush()

    with pytest.raises(BusinessRuleError, match="Expired batch cannot be sold"):
        await service.complete(sale_id=sale.id, expired_sale_confirmed=True)
