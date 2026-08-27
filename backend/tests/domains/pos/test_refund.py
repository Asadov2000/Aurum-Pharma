"""Refund flow — partial vs full, immutable parent, inventory return."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.auth.models import AppUser
from app.domains.foundation.repository import FoundationRepository
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.sync.repository import SyncOutboxRepository


async def _open_shift_and_sell(  # type: ignore[no-untyped-def]
    db_session: AsyncSession,
    scaffold,
    qty: int,
    payments: list[tuple[str, Decimal]] | None = None,
):
    """Returns (service, scaffold_dict, completed_sale, first_item)."""
    s = await scaffold(sale_price=10, batch_qty=qty * 2)
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    items, _ = await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal(qty))
    for payment_method, amount in payments or [("cash", Decimal(qty * 10))]:
        if payment_method == "cash":
            await service.add_payment(
                sale_id=sale.id,
                payment_method=payment_method,
                amount=amount,
            )
        else:
            # Refund tests also cover historical electronic sales created
            # before payment attempts became mandatory.
            await service.repo.insert_payment(
                tenant_id=s["tenant"].id,
                sale_id=sale.id,
                payment_method=payment_method,
                amount=amount,
            )
    completed = await service.complete(sale_id=sale.id)
    return service, s, completed, items[0]


async def _confirmed_refund_attempt(  # type: ignore[no-untyped-def]
    service: POSService,
    scaffold,
    parent,
    items: list[tuple[UUID, Decimal]],
    *,
    suffix: str,
):
    attempt = await service.create_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        parent_sale_id=parent.id,
        items=items,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
    )
    return await service.confirm_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        confirmations=[
            (
                payment.payment_method,
                f"terminal-{payment.payment_method}",
                f"refund-{suffix}-{payment.payment_method}",
            )
            for payment in attempt.payments
        ],
        allowed_branch_ids=None,
    )


async def test_partial_refund_does_not_void_parent(db_session: AsyncSession, pos_scaffold) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=5)
    inv_repo = InventoryRepository(db_session)
    batch_before = await inv_repo.get_batch(item.batch_id)
    assert batch_before is not None
    qty_before = batch_before.qty_remaining

    ret = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("2"))],
        reason="defect",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    assert ret.sale_type == "return"
    assert ret.parent_sale_id == parent.id
    assert ret.status == "completed"
    return_items = await POSRepository(db_session).list_items(ret.id)
    assert return_items[0].parent_sale_item_id == item.id

    # Parent stays completed — partial refund
    await db_session.refresh(parent)
    assert parent.status == "completed"
    assert parent.voided_at is None

    # Inventory came back. session.get returns the cached object; the
    # trigger updated the DB, so refresh to see the new qty.
    batch_after = await inv_repo.get_batch(item.batch_id)
    assert batch_after is not None
    await db_session.refresh(batch_after)
    assert batch_after.qty_remaining == qty_before + Decimal("2.000")


async def test_full_refund_derives_voided_state_without_mutating_parent(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=3)

    ret = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, item.qty)],
        reason="not_needed",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )

    await db_session.refresh(parent)
    assert parent.status == "completed"
    assert parent.voided_at is None
    assert parent.voided_by_sale_id is None

    lifecycle = await service.repo.sale_lifecycle(parent)
    assert lifecycle.status == "voided"
    assert lifecycle.voided_at == ret.completed_at
    assert lifecycle.voided_by_sale_id == ret.id


async def test_voided_sale_receipt_number_is_never_reused(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=2)
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, item.qty)],
        reason="full return",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )

    next_sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(
        sale_id=next_sale.id,
        catalog_id=s["item"].id,
        qty=Decimal("1"),
    )
    await service.add_payment(
        sale_id=next_sale.id,
        payment_method="cash",
        amount=Decimal("10"),
    )
    completed = await service.complete(sale_id=next_sale.id)

    assert parent.receipt_number == "000001"
    assert returned.receipt_number == "000002"
    assert completed.receipt_number == "000003"


async def test_refund_retry_and_result_recovery_are_idempotent_and_scoped(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
    )
    operation_id = uuid4()
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="customer_return",
        comment=None,
        cashier_user_id=s["cashier"].id,
        operation_id=operation_id,
    )
    retried = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="customer_return",
        comment=None,
        cashier_user_id=s["cashier"].id,
        operation_id=operation_id,
    )
    recovered = await service.get_refund_result(
        tenant_id=s["tenant"].id,
        operation_id=operation_id,
        allowed_branch_ids={s["branch"].id},
    )

    assert retried.id == returned.id
    assert recovered.id == returned.id
    assert await service.get_refunded_quantities(parent.id) == {item.id: Decimal("1.000")}
    outbox = SyncOutboxRepository(db_session)
    refund_event = await outbox.get_by_operation_id(
        tenant_id=s["tenant"].id,
        operation_id=operation_id,
    )
    assert refund_event is not None
    assert refund_event.event_type == "pos.sale.refunded.v1"
    assert refund_event.aggregate_id == returned.id
    assert refund_event.payload["parent_sale_id"] == str(parent.id)
    assert refund_event.payload["parent_fully_refunded"] is False

    retried_with_reentered_metadata = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="different_reason",
        comment="re-entered after reconnect",
        cashier_user_id=s["cashier"].id,
        operation_id=operation_id,
    )
    assert retried_with_reentered_metadata.id == returned.id
    replayed_event = await outbox.get_by_operation_id(
        tenant_id=s["tenant"].id,
        operation_id=operation_id,
    )
    assert replayed_event is not None
    assert replayed_event.event_id == refund_event.event_id
    with pytest.raises(ConflictError):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("2"))],
            reason="customer_return",
            comment=None,
            cashier_user_id=s["cashier"].id,
            operation_id=operation_id,
        )
    with pytest.raises(NotFoundError):
        await service.get_refund_result(
            tenant_id=uuid4(),
            operation_id=operation_id,
        )
    with pytest.raises(PermissionDeniedError):
        await service.get_refund_result(
            tenant_id=s["tenant"].id,
            operation_id=operation_id,
            allowed_branch_ids={uuid4()},
        )


async def test_refund_more_than_sold_blocked(db_session: AsyncSession, pos_scaffold) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=2)
    with pytest.raises(BusinessRuleError):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("99"))],
            reason="bug",
            comment=None,
            cashier_user_id=s["cashier"].id,
        )


async def test_duplicate_refund_lines_are_validated_as_one_quantity(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=3)
    with pytest.raises(BusinessRuleError):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("2")), (item.id, Decimal("2"))],
            reason="duplicate input",
            comment=None,
            cashier_user_id=s["cashier"].id,
        )


async def test_double_refund_tracks_already_refunded(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=4)
    # First refund: 2 of 4
    await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("2"))],
        reason="one",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    # Second refund: 2 more — fine
    await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("2"))],
        reason="two",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    # The persisted parent remains immutable; its read model becomes voided.
    await db_session.refresh(parent)
    assert parent.status == "completed"
    lifecycle = await service.repo.sale_lifecycle(parent)
    assert lifecycle.status == "voided"

    # A third refund must fail (nothing left)
    with pytest.raises(BusinessRuleError):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="three",
            comment=None,
            cashier_user_id=s["cashier"].id,
        )


async def test_discounted_line_refunds_never_exceed_original_net_total(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=10)
    repo = POSRepository(db_session)
    service = POSService(repo)
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    items, _ = await service.add_item(
        sale_id=sale.id,
        catalog_id=s["item"].id,
        qty=Decimal("3"),
    )
    item = items[0]
    await repo.update_item(
        item,
        discount_amount=Decimal("5.00"),
        total_price=Decimal("25.00"),
    )
    await repo.update_sale(sale, total_amount=Decimal("25.00"))
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("25.00"),
    )
    parent = await service.complete(sale_id=sale.id)

    first = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="partial one",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    second = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("2"))],
        reason="partial two",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )

    first_item = (await repo.list_items(first.id))[0]
    second_item = (await repo.list_items(second.id))[0]
    assert (first_item.total_price, first_item.discount_amount) == (
        Decimal("8.33"),
        Decimal("1.67"),
    )
    assert (second_item.total_price, second_item.discount_amount) == (
        Decimal("16.67"),
        Decimal("3.33"),
    )
    assert first.total_amount + second.total_amount == parent.total_amount

    refunded_payments = Decimal("0")
    for returned_sale in (first, second):
        refunded_payments += sum(
            (payment.amount for payment in await repo.list_payments(returned_sale.id)),
            Decimal("0"),
        )
    assert refunded_payments == Decimal("25.00")


async def test_refund_uses_current_shift_after_original_shift_closed(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
    )
    await service.close_shift(
        shift_id=parent.shift_id,
        closing_cash_actual=Decimal("20"),
        closed_by_user_id=s["cashier"].id,
    )
    current_shift = await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )

    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="customer return",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )

    assert returned.shift_id == current_shift.id
    assert returned.shift_id != parent.shift_id


async def test_non_cash_refund_requires_external_terminal_confirmation(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20"))],
    )

    with pytest.raises(BusinessRuleError, match="confirmed refund attempt"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="customer return",
            comment=None,
            cashier_user_id=s["cashier"].id,
        )

    attempt = await _confirmed_refund_attempt(
        service,
        s,
        parent,
        [(item.id, Decimal("1"))],
        suffix="card",
    )
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="customer return",
        comment=None,
        cashier_user_id=s["cashier"].id,
        refund_attempt_id=attempt.id,
    )
    payments = await service.repo.list_payments(returned.id)
    assert [(payment.payment_method, payment.amount) for payment in payments] == [
        ("card", Decimal("10.00"))
    ]
    assert payments[0].metadata_json == {
        "reason": "customer return",
        "comment": None,
        "refund_attempt_id": str(attempt.id),
    }
    assert returned.refund_attempt_id == attempt.id
    consumed = await service.get_refund_attempt(
        tenant_id=s["tenant"].id,
        attempt_id=attempt.id,
        allowed_branch_ids=None,
    )
    assert consumed.status == "consumed"


async def test_cash_refund_rejects_electronic_refund_attempt(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=1,
    )

    with pytest.raises(BusinessRuleError, match="Cash-only refunds"):
        await service.create_refund_attempt(
            tenant_id=s["tenant"].id,
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            actor_id=s["cashier"].id,
            operation_id=uuid4(),
        )


async def test_refund_attempt_create_is_idempotent_and_payload_bound(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20"))],
    )
    operation_id = uuid4()

    first = await service.create_refund_attempt(
        tenant_id=s["tenant"].id,
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        actor_id=s["cashier"].id,
        operation_id=operation_id,
    )
    retried = await service.create_refund_attempt(
        tenant_id=s["tenant"].id,
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1.000"))],
        actor_id=s["cashier"].id,
        operation_id=operation_id,
    )

    assert retried.id == first.id
    assert retried.status == "pending"
    with pytest.raises(ConflictError, match="another refund attempt"):
        await service.create_refund_attempt(
            tenant_id=s["tenant"].id,
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("2"))],
            actor_id=s["cashier"].id,
            operation_id=operation_id,
        )


async def test_refund_confirmation_requires_all_methods_and_is_idempotent(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("10")), ("qr", Decimal("10"))],
    )
    attempt = await service.create_refund_attempt(
        tenant_id=s["tenant"].id,
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        actor_id=s["cashier"].id,
        operation_id=uuid4(),
    )
    confirmations = [
        ("card", "TERM-CARD", "CARD-REFUND-001"),
        ("qr", "TERM-QR", "QR-REFUND-001"),
    ]

    with pytest.raises(BusinessRuleError, match="Every electronic refund method"):
        await service.confirm_refund_attempt(
            tenant_id=s["tenant"].id,
            attempt_id=attempt.id,
            actor_id=s["cashier"].id,
            confirmations=confirmations[:1],
            allowed_branch_ids=None,
        )

    confirmed = await service.confirm_refund_attempt(
        tenant_id=s["tenant"].id,
        attempt_id=attempt.id,
        actor_id=s["cashier"].id,
        confirmations=confirmations,
        allowed_branch_ids=None,
    )
    retried = await service.confirm_refund_attempt(
        tenant_id=s["tenant"].id,
        attempt_id=attempt.id,
        actor_id=s["cashier"].id,
        confirmations=list(reversed(confirmations)),
        allowed_branch_ids=None,
    )

    assert retried.id == confirmed.id
    assert retried.status == "confirmed"
    assert {payment.payment_method for payment in retried.payments} == {"card", "qr"}
    with pytest.raises(ConflictError, match="other terminal documents"):
        await service.confirm_refund_attempt(
            tenant_id=s["tenant"].id,
            attempt_id=attempt.id,
            actor_id=s["cashier"].id,
            confirmations=[
                ("card", "TERM-CARD", "CARD-REFUND-CHANGED"),
                ("qr", "TERM-QR", "QR-REFUND-001"),
            ],
            allowed_branch_ids=None,
        )


async def test_terminal_refund_document_cannot_be_reused_for_another_sale(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20"))],
    )
    first = await service.create_refund_attempt(
        tenant_id=s["tenant"].id,
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        actor_id=s["cashier"].id,
        operation_id=uuid4(),
    )
    await service.confirm_refund_attempt(
        tenant_id=s["tenant"].id,
        attempt_id=first.id,
        actor_id=s["cashier"].id,
        confirmations=[("card", "TERM-01", "REFUND-DOC-01")],
        allowed_branch_ids=None,
    )
    second_sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    second_items, _ = await service.add_item(
        sale_id=second_sale.id,
        catalog_id=s["item"].id,
        qty=Decimal("1"),
    )
    await service.repo.insert_payment(
        tenant_id=s["tenant"].id,
        sale_id=second_sale.id,
        payment_method="card",
        amount=Decimal("10"),
    )
    second_parent = await service.complete(sale_id=second_sale.id)
    second = await service.create_refund_attempt(
        tenant_id=s["tenant"].id,
        parent_sale_id=second_parent.id,
        items=[(second_items[0].id, Decimal("1"))],
        actor_id=s["cashier"].id,
        operation_id=uuid4(),
    )

    with pytest.raises(ConflictError, match="already used"):
        async with db_session.begin_nested():
            await service.confirm_refund_attempt(
                tenant_id=s["tenant"].id,
                attempt_id=second.id,
                actor_id=s["cashier"].id,
                confirmations=[("card", "TERM-01", "REFUND-DOC-01")],
                allowed_branch_ids=None,
            )

    with pytest.raises(BusinessRuleError, match="must be completed"):
        await service.void_refund_attempt(
            tenant_id=s["tenant"].id,
            attempt_id=first.id,
            actor_id=s["cashier"].id,
            reason="refund_failed",
            operator_note=None,
            can_manage=True,
            allowed_branch_ids=None,
        )


async def test_shift_close_waits_until_refund_attempt_is_resolved(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=1,
        payments=[("card", Decimal("10"))],
    )
    attempt = await service.create_refund_attempt(
        tenant_id=s["tenant"].id,
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        actor_id=s["cashier"].id,
        operation_id=uuid4(),
    )

    with pytest.raises(BusinessRuleError, match="refund attempt is unresolved"):
        await service.close_shift(
            shift_id=parent.shift_id,
            closing_cash_actual=Decimal("0"),
            closed_by_user_id=s["cashier"].id,
        )

    await service.void_refund_attempt(
        tenant_id=s["tenant"].id,
        attempt_id=attempt.id,
        actor_id=s["cashier"].id,
        reason="customer_cancelled",
        operator_note=None,
        can_manage=False,
        allowed_branch_ids=None,
    )
    closed = await service.close_shift(
        shift_id=parent.shift_id,
        closing_cash_actual=Decimal("0"),
        closed_by_user_id=s["cashier"].id,
    )
    assert closed.status == "closed"


async def test_partial_refund_preserves_original_mixed_tender_ratio(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=10,
        payments=[("cash", Decimal("30")), ("card", Decimal("70"))],
    )

    attempt = await _confirmed_refund_attempt(
        service,
        s,
        parent,
        [(item.id, Decimal("3"))],
        suffix="mixed",
    )
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("3"))],
        reason="partial",
        comment=None,
        cashier_user_id=s["cashier"].id,
        refund_attempt_id=attempt.id,
    )
    payments = await service.repo.list_payments(returned.id)

    assert {payment.payment_method: payment.amount for payment in payments} == {
        "cash": Decimal("9.00"),
        "card": Decimal("21.00"),
    }


async def test_repeated_mixed_refunds_reconcile_to_original_tender_totals(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(sale_price=1, batch_qty=10)
    repo = POSRepository(db_session)
    service = POSService(repo)
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    items, _ = await service.add_item(
        sale_id=sale.id,
        catalog_id=s["item"].id,
        qty=Decimal("3"),
    )
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("1.00"),
    )
    await repo.insert_payment(
        tenant_id=s["tenant"].id,
        sale_id=sale.id,
        payment_method="card",
        amount=Decimal("2.00"),
    )
    parent = await service.complete(sale_id=sale.id)

    for index in range(3):
        attempt = await _confirmed_refund_attempt(
            service,
            s,
            parent,
            [(items[0].id, Decimal("1"))],
            suffix=f"repeat-{index}",
        )
        await service.refund(
            parent_sale_id=parent.id,
            items=[(items[0].id, Decimal("1"))],
            reason=f"partial {index + 1}",
            comment=None,
            cashier_user_id=s["cashier"].id,
            refund_attempt_id=attempt.id,
        )

    assert await repo.refunded_payment_totals(parent.id) == {
        "cash": Decimal("1.00"),
        "card": Decimal("2.00"),
    }


async def test_refund_cannot_use_another_cashiers_shift_without_manage_permission(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
    )
    manager = AppUser(
        email=f"manager-{uuid4().hex[:8]}@aurum.tj",
        full_name="Manager",
        home_tenant_id=s["tenant"].id,
        status="active",
    )
    db_session.add(manager)
    await db_session.flush()

    with pytest.raises(PermissionDeniedError, match="another cashier"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="approved",
            comment=None,
            cashier_user_id=manager.id,
        )

    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="approved",
        comment=None,
        cashier_user_id=manager.id,
        can_manage_tenant=True,
    )
    assert returned.cashier_user_id == manager.id


async def test_refund_reason_policy_is_enforced_by_service(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
    )
    foundation = FoundationRepository(db_session)
    settings = await foundation.get_settings(s["tenant"].id)
    assert settings is not None
    await foundation.update_settings(settings, refund_reason_mode="required_with_text")

    with pytest.raises(BusinessRuleError, match="reason is required"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason=None,
            comment=None,
            cashier_user_id=s["cashier"].id,
        )
    with pytest.raises(BusinessRuleError, match="comment is required"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="defect",
            comment="  ",
            cashier_user_id=s["cashier"].id,
        )

    await foundation.update_settings(settings, refund_reason_mode="off")
    with pytest.raises(BusinessRuleError, match="disabled"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="not allowed",
            comment=None,
            cashier_user_id=s["cashier"].id,
        )

    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason=None,
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    assert returned.status == "completed"
