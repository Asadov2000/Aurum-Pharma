"""Atomic checkout invariants on the real PostgreSQL schema."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.audit.models import AuditLog
from app.domains.inventory.models import BatchMovement
from app.domains.pos.models import PrescriptionLog, Sale, SalePayment
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.sync.models import SyncOutboxEvent
from app.domains.sync.repository import SyncOutboxRepository


async def _open_shift(service: POSService, scaffold) -> None:  # type: ignore[no-untyped-def]
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )


async def _count(session: AsyncSession, model: type[object], *where: object) -> int:
    stmt = select(func.count()).select_from(model)
    for criterion in where:
        stmt = stmt.where(criterion)
    return int((await session.execute(stmt)).scalar_one())


async def test_atomic_checkout_commits_one_stable_result(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    operation_id = uuid4()

    first = await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
        items=[(scaffold["item"].id, Decimal("2"))],
        payments=[("cash", Decimal("20"), None)],
    )
    retried = await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
        items=[(scaffold["item"].id, Decimal("2.000"))],
        payments=[("cash", Decimal("20.00"), None)],
    )

    assert retried == first
    assert first.receipt_number == "000001"
    assert first.total_amount == Decimal("20.00")
    assert [(item.batch_id, item.qty) for item in first.items] == [
        (scaffold["batch"].id, Decimal("2.000"))
    ]
    assert [(payment.payment_method, payment.amount) for payment in first.payments] == [
        ("cash", Decimal("20.00"))
    ]

    await db_session.refresh(scaffold["batch"])
    assert scaffold["batch"].qty_remaining == Decimal("98.000")
    assert await _count(db_session, Sale, Sale.operation_id == operation_id) == 1
    assert await _count(db_session, SalePayment, SalePayment.sale_id == first.sale_id) == 1
    assert (
        await _count(
            db_session,
            BatchMovement,
            BatchMovement.source_id == first.sale_id,
            BatchMovement.movement_type == "sale",
        )
        == 1
    )
    assert (
        await _count(
            db_session,
            SyncOutboxEvent,
            SyncOutboxEvent.operation_id == operation_id,
        )
        == 1
    )
    event = await SyncOutboxRepository(db_session).get_by_operation_id(
        tenant_id=scaffold["tenant"].id,
        operation_id=operation_id,
    )
    assert event is not None
    assert event.payload == first.model_dump(mode="json")


async def test_atomic_checkout_rebuilds_draft_and_recovers_by_operation(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    draft = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    old_items, _requires_rx = await service.add_item(
        sale_id=draft.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
        actor_id=scaffold["cashier"].id,
    )
    operation_id = uuid4()

    completed = await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
        draft_sale_id=draft.id,
        items=[(scaffold["item"].id, Decimal("2"))],
        payments=[("cash", Decimal("20"), None)],
    )
    recovered = await service.get_checkout_result(
        tenant_id=scaffold["tenant"].id,
        operation_id=operation_id,
        actor_id=scaffold["cashier"].id,
    )

    assert recovered == completed
    assert completed.sale_id == draft.id
    assert [item.qty for item in completed.items] == [Decimal("2.000")]
    assert old_items[0].id not in {item.id for item in completed.items}
    assert await _count(db_session, Sale, Sale.id == draft.id) == 1


async def test_atomic_checkout_changed_payload_conflicts_without_second_effect(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    operation_id = uuid4()

    first = await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
        items=[(scaffold["item"].id, Decimal("1"))],
        payments=[("cash", Decimal("10"), None)],
    )
    with pytest.raises(ConflictError):
        await service.checkout(
            tenant_id=scaffold["tenant"].id,
            register_id=scaffold["register"].id,
            cashier_user_id=scaffold["cashier"].id,
            operation_id=operation_id,
            items=[(scaffold["item"].id, Decimal("2"))],
            payments=[("cash", Decimal("20"), None)],
        )

    await db_session.refresh(scaffold["batch"])
    assert scaffold["batch"].qty_remaining == Decimal("99.000")
    assert await _count(db_session, Sale, Sale.operation_id == operation_id) == 1
    assert await _count(db_session, SalePayment, SalePayment.sale_id == first.sale_id) == 1
    assert (
        await _count(
            db_session,
            SyncOutboxEvent,
            SyncOutboxEvent.operation_id == operation_id,
        )
        == 1
    )


@pytest.mark.parametrize(
    ("qty", "payment"),
    [
        (Decimal("101"), Decimal("1010")),
        (Decimal("2"), Decimal("19")),
    ],
)
async def test_atomic_checkout_business_failure_rolls_back_every_row(
    db_session: AsyncSession,
    pos_scaffold,
    qty: Decimal,
    payment: Decimal,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    operation_id = uuid4()

    with pytest.raises(BusinessRuleError):
        async with db_session.begin_nested():
            await service.checkout(
                tenant_id=scaffold["tenant"].id,
                register_id=scaffold["register"].id,
                cashier_user_id=scaffold["cashier"].id,
                operation_id=operation_id,
                items=[(scaffold["item"].id, qty)],
                payments=[("cash", payment, None)],
            )

    db_session.expire_all()
    await db_session.refresh(scaffold["batch"])
    assert scaffold["batch"].qty_remaining == Decimal("100.000")
    assert await _count(db_session, Sale, Sale.operation_id == operation_id) == 0
    assert (
        await _count(
            db_session,
            SyncOutboxEvent,
            SyncOutboxEvent.operation_id == operation_id,
        )
        == 0
    )
    assert (
        await _count(
            db_session,
            BatchMovement,
            BatchMovement.batch_id == scaffold["batch"].id,
            BatchMovement.movement_type == "sale",
        )
        == 0
    )


async def test_atomic_checkout_rx_snapshot_excludes_patient_data(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold(dispensing_type="prescription")
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    operation_id = uuid4()
    patient_name = "Sensitive Patient"

    result = await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
        items=[(scaffold["item"].id, Decimal("1"))],
        payments=[("cash", Decimal("10"), {"terminal_reference": "T-1"})],
        prescription={
            "prescription_number": "RX-1",
            "doctor_name": "Doctor",
            "patient_name": patient_name,
        },
    )

    assert await _count(db_session, PrescriptionLog, PrescriptionLog.sale_id == result.sale_id) == 1
    event = await SyncOutboxRepository(db_session).get_by_operation_id(
        tenant_id=scaffold["tenant"].id,
        operation_id=operation_id,
    )
    assert event is not None
    serialized = json.dumps(event.payload, ensure_ascii=False)
    assert patient_name not in serialized
    assert "prescription_number" not in serialized
    assert "terminal_reference" not in serialized


async def test_atomic_checkout_operation_id_is_tenant_scoped(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    first_tenant = await pos_scaffold()
    second_tenant = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    await _open_shift(service, first_tenant)
    await _open_shift(service, second_tenant)
    operation_id = uuid4()

    first = await service.checkout(
        tenant_id=first_tenant["tenant"].id,
        register_id=first_tenant["register"].id,
        cashier_user_id=first_tenant["cashier"].id,
        operation_id=operation_id,
        items=[(first_tenant["item"].id, Decimal("1"))],
        payments=[("cash", Decimal("10"), None)],
    )
    second = await service.checkout(
        tenant_id=second_tenant["tenant"].id,
        register_id=second_tenant["register"].id,
        cashier_user_id=second_tenant["cashier"].id,
        operation_id=operation_id,
        items=[(second_tenant["item"].id, Decimal("1"))],
        payments=[("cash", Decimal("10"), None)],
    )

    assert first.sale_id != second.sale_id
    assert first.event_id != second.event_id
    assert await _count(db_session, Sale, Sale.operation_id == operation_id) == 2
    assert (
        await _count(
            db_session,
            SyncOutboxEvent,
            SyncOutboxEvent.operation_id == operation_id,
        )
        == 2
    )


async def test_atomic_checkout_late_outbox_failure_rolls_back_savepoint(
    db_session: AsyncSession,
    pos_scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    operation_id = uuid4()
    tenant_id = scaffold["tenant"].id

    async def _fail_enqueue(self: SyncOutboxRepository, **fields: object) -> SyncOutboxEvent:
        del self, fields
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(SyncOutboxRepository, "enqueue", _fail_enqueue)
    with pytest.raises(RuntimeError, match="injected outbox failure"):
        async with db_session.begin_nested():
            await service.checkout(
                tenant_id=scaffold["tenant"].id,
                register_id=scaffold["register"].id,
                cashier_user_id=scaffold["cashier"].id,
                operation_id=operation_id,
                items=[(scaffold["item"].id, Decimal("2"))],
                payments=[("cash", Decimal("20"), None)],
            )

    db_session.expire_all()
    await db_session.refresh(scaffold["batch"])
    assert scaffold["batch"].qty_remaining == Decimal("100.000")
    assert await _count(db_session, Sale, Sale.operation_id == operation_id) == 0
    assert (
        await _count(
            db_session,
            AuditLog,
            AuditLog.tenant_id == tenant_id,
            AuditLog.table_name == "sale",
        )
        == 0
    )
    assert (
        await _count(
            db_session,
            BatchMovement,
            BatchMovement.batch_id == scaffold["batch"].id,
            BatchMovement.movement_type == "sale",
        )
        == 0
    )
