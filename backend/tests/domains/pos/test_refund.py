"""Refund flow — partial vs full, immutable parent, inventory return."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domains.audit.models import AuditLog
from app.domains.auth.models import AppUser
from app.domains.customer_returns.models import CustomerReturnQuarantineItem
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import RefundCreate
from app.domains.pos.service import POSService
from app.domains.sync.models import SyncOutboxEvent
from app.domains.sync.repository import SyncOutboxRepository
from app.main import app


def test_refund_schema_accepts_only_controlled_reason_and_normalizes_comment() -> None:
    item_id = uuid4()
    operation_id = uuid4()

    valid = RefundCreate(
        operation_id=operation_id,
        items=[{"sale_item_id": item_id, "qty": "1"}],
        reason="quality_issue",
        comment="  Упаковка повреждена при передаче  ",
    )
    assert valid.reason == "quality_issue"
    assert valid.comment == "Упаковка повреждена при передаче"

    with pytest.raises(ValidationError, match="Input should be"):
        RefundCreate(
            operation_id=operation_id,
            items=[{"sale_item_id": item_id, "qty": "1"}],
            reason="произвольный текст",
        )


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
    reason: str | None = "other",
    comment: str | None = None,
):
    attempt = await service.create_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        parent_sale_id=parent.id,
        items=items,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        reason=reason,
        comment=comment,
    )
    await service.begin_refund_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        allowed_branch_ids=None,
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


async def _complete_historical_electronic_sale(  # type: ignore[no-untyped-def]
    service: POSService,
    scaffold,
    *,
    register_id: UUID | None = None,
    payments: list[tuple[str, Decimal]] | None = None,
):
    sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=register_id or scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    items, _ = await service.add_item(
        sale_id=sale.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
    )
    for payment_method, amount in payments or [("card", Decimal("10.00"))]:
        await service.repo.insert_payment(
            tenant_id=scaffold["tenant"].id,
            sale_id=sale.id,
            payment_method=payment_method,
            amount=amount,
        )
    return await service.complete(sale_id=sale.id), items[0]


async def _create_refund_attempt(  # type: ignore[no-untyped-def]
    service: POSService,
    scaffold,
    parent,
    item,
    *,
    reason: str | None = "other",
    comment: str | None = None,
):
    return await service.create_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        reason=reason,
        comment=comment,
    )


async def _close_refund_attempts(  # type: ignore[no-untyped-def]
    service: POSService,
    scaffold,
    voided_sale,
    consumed_sale,
) -> None:
    voided = await _create_refund_attempt(service, scaffold, *voided_sale)
    await service.void_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=voided.id,
        actor_id=scaffold["cashier"].id,
        reason="customer_cancelled",
        operator_note=None,
        can_manage_tenant=False,
        allowed_branch_ids=None,
        allowed_manage_branch_ids=set(),
    )
    consumed = await _create_refund_attempt(service, scaffold, *consumed_sale)
    await service.begin_refund_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=consumed.id,
        actor_id=scaffold["cashier"].id,
        allowed_branch_ids=None,
    )
    consumed = await service.confirm_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=consumed.id,
        actor_id=scaffold["cashier"].id,
        confirmations=[("card", "CARD-TERM", "CARD-REFUND-CONSUMED")],
        allowed_branch_ids=None,
    )
    await service.refund(
        parent_sale_id=consumed_sale[0].id,
        items=[(consumed_sale[1].id, Decimal("1"))],
        reason="other",
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        refund_attempt_id=consumed.id,
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
        reason="other",
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

    # A customer-returned medicine is isolated from saleable inventory.
    batch_after = await inv_repo.get_batch(item.batch_id)
    assert batch_after is not None
    await db_session.refresh(batch_after)
    assert batch_after.qty_remaining == qty_before
    quarantine_count = await db_session.scalar(
        select(func.count(CustomerReturnQuarantineItem.id)).where(
            CustomerReturnQuarantineItem.tenant_id == s["tenant"].id,
            CustomerReturnQuarantineItem.return_sale_id == ret.id,
        )
    )
    assert quarantine_count == 1


async def test_full_refund_derives_voided_state_without_mutating_parent(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=3)

    ret = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, item.qty)],
        reason="other",
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
        reason="other",
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
        reason="other",
        comment=None,
        cashier_user_id=s["cashier"].id,
        operation_id=operation_id,
    )
    retried = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="other",
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

    with pytest.raises(ConflictError, match="another refund"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="other",
            comment="re-entered after reconnect",
            cashier_user_id=s["cashier"].id,
            operation_id=operation_id,
        )
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
            reason="other",
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


async def test_electronic_refund_recovery_rejects_missing_outbox_snapshot(
    db_session: AsyncSession,
    pos_scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, scaffold, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20"))],
    )
    attempt = await _confirmed_refund_attempt(
        service,
        scaffold,
        parent,
        [(item.id, Decimal("1"))],
        suffix="missing-outbox",
    )
    operation_id = uuid4()
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="other",
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
        refund_attempt_id=attempt.id,
    )

    async def _missing_snapshot(
        self: SyncOutboxRepository,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> None:
        del self, tenant_id, operation_id

    monkeypatch.setattr(SyncOutboxRepository, "get_by_operation_id", _missing_snapshot)
    with pytest.raises(AurumError, match="snapshot is unavailable"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="other",
            comment=None,
            cashier_user_id=scaffold["cashier"].id,
            operation_id=operation_id,
            refund_attempt_id=attempt.id,
        )
    with pytest.raises(AurumError, match="snapshot is unavailable"):
        await service.get_refund_result(
            tenant_id=scaffold["tenant"].id,
            operation_id=operation_id,
        )

    assert returned.status == "completed"


async def test_electronic_refund_outbox_failure_rolls_back_and_remains_retryable(
    db_session: AsyncSession,
    pos_scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, scaffold, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20"))],
    )
    attempt = await _confirmed_refund_attempt(
        service,
        scaffold,
        parent,
        [(item.id, Decimal("1"))],
        suffix="rollback",
    )
    tenant_id = scaffold["tenant"].id
    cashier_id = scaffold["cashier"].id
    parent_id = parent.id
    item_id = item.id
    attempt_id = attempt.id
    operation_id = uuid4()

    async def _fail_enqueue(
        self: SyncOutboxRepository,
        **fields: object,
    ) -> SyncOutboxEvent:
        del self, fields
        raise RuntimeError("injected refund outbox failure")

    monkeypatch.setattr(SyncOutboxRepository, "enqueue", _fail_enqueue)
    with pytest.raises(RuntimeError, match="injected refund outbox failure"):
        async with db_session.begin_nested():
            await service.refund(
                parent_sale_id=parent_id,
                items=[(item_id, Decimal("1"))],
                reason="other",
                comment=None,
                cashier_user_id=cashier_id,
                operation_id=operation_id,
                refund_attempt_id=attempt_id,
            )

    db_session.expire_all()
    assert (
        await service.repo.get_sale_by_operation_id(
            tenant_id=tenant_id,
            operation_id=operation_id,
        )
        is None
    )
    assert await service.get_refunded_quantities(parent_id) == {}
    restored_attempt = await service.get_refund_attempt(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        allowed_branch_ids=None,
    )
    assert restored_attempt.status == "confirmed"
    assert restored_attempt.consumed_at is None

    monkeypatch.undo()
    returned = await service.refund(
        parent_sale_id=parent_id,
        items=[(item_id, Decimal("1"))],
        reason="other",
        comment=None,
        cashier_user_id=cashier_id,
        operation_id=operation_id,
        refund_attempt_id=attempt_id,
    )
    event = await SyncOutboxRepository(db_session).get_by_operation_id(
        tenant_id=tenant_id,
        operation_id=operation_id,
    )
    recovered = await service.get_refund_result(
        tenant_id=tenant_id,
        operation_id=operation_id,
    )
    consumed_attempt = await service.get_refund_attempt(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        allowed_branch_ids=None,
    )
    assert event is not None
    assert event.aggregate_id == returned.id
    assert recovered.id == returned.id
    assert consumed_attempt.status == "consumed"


async def test_consumed_attempt_without_completed_refund_blocks_recovery_and_repeat(
    db_session: AsyncSession,
    pos_scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, scaffold, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20"))],
    )
    attempt = await _confirmed_refund_attempt(
        service,
        scaffold,
        parent,
        [(item.id, Decimal("1"))],
        suffix="orphan-consumed",
    )
    await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="other",
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        refund_attempt_id=attempt.id,
    )

    async def _missing_return(
        *,
        tenant_id: UUID,
        refund_attempt_id: UUID,
    ) -> None:
        assert tenant_id == scaffold["tenant"].id
        assert refund_attempt_id == attempt.id

    async def _incomplete_history(
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
    ) -> bool:
        assert tenant_id == scaffold["tenant"].id
        assert parent_sale_id == parent.id
        return True

    monkeypatch.setattr(service.repo, "get_return_by_refund_attempt_id", _missing_return)
    monkeypatch.setattr(
        service.repo,
        "has_incomplete_consumed_refund_history",
        _incomplete_history,
    )
    with pytest.raises(AurumError, match="no completed refund"):
        await service.get_refund_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            allowed_branch_ids=None,
        )

    retry_operation_id = uuid4()
    with pytest.raises(AurumError, match="history is incomplete"):
        await service.create_refund_attempt(
            tenant_id=scaffold["tenant"].id,
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            actor_id=scaffold["cashier"].id,
            operation_id=retry_operation_id,
        )
    assert (
        await service.repo.get_refund_attempt_by_operation_id(
            tenant_id=scaffold["tenant"].id,
            operation_id=retry_operation_id,
        )
        is None
    )


async def test_confirmed_refund_attempt_requires_all_terminal_references_at_finalize(
    db_session: AsyncSession,
    pos_scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, scaffold, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("10")), ("qr", Decimal("10"))],
    )
    attempt = await _confirmed_refund_attempt(
        service,
        scaffold,
        parent,
        [(item.id, Decimal("1"))],
        suffix="incomplete-references",
    )
    references = await service.repo.list_refund_references(attempt.id)

    async def _missing_reference(
        attempt_id: UUID,
    ):  # type: ignore[no-untyped-def]
        assert attempt_id == attempt.id
        return references[:-1]

    monkeypatch.setattr(service.repo, "list_refund_references", _missing_reference)
    operation_id = uuid4()
    with pytest.raises(AurumError, match="terminal references are incomplete"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="other",
            comment=None,
            cashier_user_id=scaffold["cashier"].id,
            operation_id=operation_id,
            refund_attempt_id=attempt.id,
        )

    assert (
        await service.repo.get_sale_by_operation_id(
            tenant_id=scaffold["tenant"].id,
            operation_id=operation_id,
        )
        is None
    )
    assert (
        await SyncOutboxRepository(db_session).get_by_operation_id(
            tenant_id=scaffold["tenant"].id,
            operation_id=operation_id,
        )
        is None
    )


async def test_refund_recovery_rejects_outbox_snapshot_that_differs_from_sale(
    db_session: AsyncSession,
    pos_scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, scaffold, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
    )
    operation_id = uuid4()
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="other",
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
    )
    event = await SyncOutboxRepository(db_session).get_by_operation_id(
        tenant_id=scaffold["tenant"].id,
        operation_id=operation_id,
    )
    assert event is not None
    mismatched_payload = {**event.payload, "receipt_number": "MISMATCHED-RECEIPT"}
    mismatched_event = cast(
        SyncOutboxEvent,
        SimpleNamespace(
            event_id=event.event_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            event_type=event.event_type,
            schema_version=event.schema_version,
            payload=mismatched_payload,
            payload_hash=service._checkout_result_hash(mismatched_payload),
        ),
    )

    async def _mismatched_snapshot(
        self: SyncOutboxRepository,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> SyncOutboxEvent:
        del self, tenant_id, operation_id
        return mismatched_event

    monkeypatch.setattr(SyncOutboxRepository, "get_by_operation_id", _mismatched_snapshot)
    with pytest.raises(AurumError, match="does not match the sale"):
        await service.get_refund_result(
            tenant_id=scaffold["tenant"].id,
            operation_id=operation_id,
        )
    with pytest.raises(AurumError, match="does not match the sale"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="other",
            comment=None,
            cashier_user_id=scaffold["cashier"].id,
            operation_id=operation_id,
        )

    assert returned.status == "completed"


async def test_refund_more_than_sold_blocked(db_session: AsyncSession, pos_scaffold) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=2)
    with pytest.raises(BusinessRuleError):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("99"))],
            reason="other",
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
            reason="other",
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
        reason="other",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    # Second refund: 2 more — fine
    await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("2"))],
        reason="other",
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
            reason="other",
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
        reason="other",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    second = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("2"))],
        reason="other",
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
        reason="other",
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
            reason="other",
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
    with pytest.raises(ConflictError, match="confirmed refund attempt"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="pricing_error",
            comment=None,
            cashier_user_id=s["cashier"].id,
            refund_attempt_id=attempt.id,
        )
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="other",
        comment=None,
        cashier_user_id=s["cashier"].id,
        refund_attempt_id=attempt.id,
    )
    payments = await service.repo.list_payments(returned.id)
    assert [(payment.payment_method, payment.amount) for payment in payments] == [
        ("card", Decimal("10.00"))
    ]
    assert payments[0].metadata_json == {
        "reason_code": "other",
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
        reason="quality_issue",
        comment="Упаковка повреждена",
    )
    retried = await service.create_refund_attempt(
        tenant_id=s["tenant"].id,
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1.000"))],
        actor_id=s["cashier"].id,
        operation_id=operation_id,
        reason="quality_issue",
        comment="Упаковка повреждена",
    )

    assert retried.id == first.id
    assert retried.status == "pending"
    assert retried.intent_locked is True
    assert retried.reason == "quality_issue"
    assert retried.comment == "Упаковка повреждена"
    with pytest.raises(DBAPIError, match="Refund attempt identity is immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE pos_refund_attempt "
                    "SET reason_code = 'pricing_error', intent_version = 1 "
                    "WHERE id = :attempt_id"
                ),
                {"attempt_id": first.id},
            )
    with pytest.raises(ConflictError, match="another refund attempt"):
        await service.create_refund_attempt(
            tenant_id=s["tenant"].id,
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("2"))],
            actor_id=s["cashier"].id,
            operation_id=operation_id,
            reason="quality_issue",
            comment="Упаковка повреждена",
        )
    with pytest.raises(ConflictError, match="another refund attempt"):
        await service.create_refund_attempt(
            tenant_id=s["tenant"].id,
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            actor_id=s["cashier"].id,
            operation_id=operation_id,
            reason="pricing_error",
            comment="Упаковка повреждена",
        )


async def test_refund_attempt_operation_id_cannot_be_reused_for_payment_attempt(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, scaffold, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20"))],
    )
    operation_id = uuid4()
    await service.create_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        actor_id=scaffold["cashier"].id,
        operation_id=operation_id,
    )
    draft = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    await service.add_item(
        sale_id=draft.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
        actor_id=scaffold["cashier"].id,
    )

    with pytest.raises(ConflictError, match="another POS operation"):
        await service.create_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            sale_id=draft.id,
            actor_id=scaffold["cashier"].id,
            operation_id=operation_id,
            payment_method="card",
            amount=Decimal("10.00"),
            currency="TJS",
        )

    with pytest.raises(ConflictError, match="another POS operation"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="other",
            comment=None,
            cashier_user_id=scaffold["cashier"].id,
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
    with pytest.raises(BusinessRuleError, match="must start reconciliation"):
        await service.confirm_refund_attempt(
            tenant_id=s["tenant"].id,
            attempt_id=attempt.id,
            actor_id=s["cashier"].id,
            confirmations=confirmations,
            allowed_branch_ids=None,
        )
    started = await service.begin_refund_attempt_reconciliation(
        tenant_id=s["tenant"].id,
        attempt_id=attempt.id,
        actor_id=s["cashier"].id,
        allowed_branch_ids=None,
    )
    retried_start = await service.begin_refund_attempt_reconciliation(
        tenant_id=s["tenant"].id,
        attempt_id=attempt.id,
        actor_id=s["cashier"].id,
        allowed_branch_ids=None,
    )
    assert started.status == "requires_reconciliation"
    assert retried_start.id == started.id
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
    await service.begin_refund_attempt_reconciliation(
        tenant_id=s["tenant"].id,
        attempt_id=first.id,
        actor_id=s["cashier"].id,
        allowed_branch_ids=None,
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
    await service.begin_refund_attempt_reconciliation(
        tenant_id=s["tenant"].id,
        attempt_id=second.id,
        actor_id=s["cashier"].id,
        allowed_branch_ids=None,
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
            can_manage_tenant=True,
            allowed_branch_ids=None,
            allowed_manage_branch_ids=None,
        )


async def test_payment_terminal_document_cannot_be_reused_for_refund(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, scaffold, parent, parent_item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20"))],
    )
    draft = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    await service.add_item(
        sale_id=draft.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
        actor_id=scaffold["cashier"].id,
    )
    payment_attempt = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=draft.id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )
    await service.begin_payment_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=payment_attempt.id,
        actor_id=scaffold["cashier"].id,
    )
    await service.confirm_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=payment_attempt.id,
        actor_id=scaffold["cashier"].id,
        terminal_id="TERM-CROSS-TYPE",
        external_reference="DOC-CROSS-TYPE-PAYMENT",
    )

    refund_attempt = await service.create_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        parent_sale_id=parent.id,
        items=[(parent_item.id, Decimal("1"))],
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
    )
    await service.begin_refund_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=refund_attempt.id,
        actor_id=scaffold["cashier"].id,
        allowed_branch_ids=None,
    )
    tenant_id = scaffold["tenant"].id
    cashier_id = scaffold["cashier"].id
    refund_attempt_id = refund_attempt.id

    with pytest.raises(ConflictError, match="already used"):
        async with db_session.begin_nested():
            await service.confirm_refund_attempt(
                tenant_id=tenant_id,
                attempt_id=refund_attempt_id,
                actor_id=cashier_id,
                confirmations=[("card", "TERM-CROSS-TYPE", "DOC-CROSS-TYPE-PAYMENT")],
                allowed_branch_ids=None,
            )

    db_session.expire_all()
    restored = await service.get_refund_attempt(
        tenant_id=tenant_id,
        attempt_id=refund_attempt_id,
        allowed_branch_ids=None,
    )
    assert restored.status == "requires_reconciliation"
    assert all(payment.document_number is None for payment in restored.payments)


async def test_refund_terminal_document_cannot_be_reused_for_payment(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, scaffold, parent, parent_item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20"))],
    )
    refund_attempt = await service.create_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        parent_sale_id=parent.id,
        items=[(parent_item.id, Decimal("1"))],
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
    )
    await service.begin_refund_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=refund_attempt.id,
        actor_id=scaffold["cashier"].id,
        allowed_branch_ids=None,
    )
    await service.confirm_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=refund_attempt.id,
        actor_id=scaffold["cashier"].id,
        confirmations=[("card", "TERM-CROSS-TYPE", "DOC-CROSS-TYPE-REFUND")],
        allowed_branch_ids=None,
    )

    draft = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    await service.add_item(
        sale_id=draft.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
        actor_id=scaffold["cashier"].id,
    )
    payment_attempt = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=draft.id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )
    await service.begin_payment_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=payment_attempt.id,
        actor_id=scaffold["cashier"].id,
    )
    tenant_id = scaffold["tenant"].id
    cashier_id = scaffold["cashier"].id
    payment_attempt_id = payment_attempt.id

    with pytest.raises(ConflictError, match="already used"):
        async with db_session.begin_nested():
            await service.confirm_payment_attempt(
                tenant_id=tenant_id,
                attempt_id=payment_attempt_id,
                actor_id=cashier_id,
                terminal_id="TERM-CROSS-TYPE",
                external_reference="DOC-CROSS-TYPE-REFUND",
            )

    db_session.expire_all()
    restored = await service.get_payment_attempt(
        tenant_id=tenant_id,
        attempt_id=payment_attempt_id,
        actor_id=cashier_id,
    )
    assert restored.status == "requires_reconciliation"
    assert restored.external_reference is None


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
        can_manage_tenant=False,
        allowed_branch_ids=None,
        allowed_manage_branch_ids=set(),
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
        reason="other",
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
            reason="other",
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
            reason="other",
            comment=None,
            cashier_user_id=manager.id,
        )

    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="other",
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

    with pytest.raises(BusinessRuleError, match="Unsupported refund reason code"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="free text is not a controlled code",
            comment="Internal explanation",
            cashier_user_id=s["cashier"].id,
        )

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
            reason="other",
            comment="  ",
            cashier_user_id=s["cashier"].id,
        )

    await foundation.update_settings(settings, refund_reason_mode="off")
    with pytest.raises(BusinessRuleError, match="disabled"):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="other",
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


async def test_refund_comment_is_redacted_from_audit_and_payment_metadata(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, scaffold, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
    )
    foundation = FoundationRepository(db_session)
    settings = await foundation.get_settings(scaffold["tenant"].id)
    assert settings is not None
    await foundation.update_settings(settings, refund_reason_mode="required_with_text")

    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="quality_issue",
        comment="Visible only in the restricted quarantine workflow",
        cashier_user_id=scaffold["cashier"].id,
    )

    payments = await service.repo.list_payments(returned.id)
    assert payments[0].metadata_json is not None
    assert payments[0].metadata_json["reason_code"] == "quality_issue"
    assert "comment" not in payments[0].metadata_json

    quarantine_audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.table_name == "customer_return_quarantine_item",
                    AuditLog.action == "INSERT",
                )
            )
        )
        .scalars()
        .all()
    )
    matching = next(
        entry
        for entry in quarantine_audit
        if entry.metadata_json is not None
        and entry.metadata_json.get("return_sale_id") == str(returned.id)
    )
    assert matching.metadata_json is not None
    assert matching.metadata_json["refund_reason"] == "quality_issue"
    assert "refund_comment" not in matching.metadata_json


async def test_refund_reconciliation_queue_lists_all_active_states_and_summary(
    client: AsyncClient,
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold(batch_qty=20, sale_price=10)
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )

    sales = []
    for payments in (
        [
            ("card", Decimal("3.00")),
            ("qr", Decimal("4.00")),
            ("bank_transfer", Decimal("3.00")),
        ],
        [("card", Decimal("10.00"))],
        [("qr", Decimal("10.00"))],
        [("card", Decimal("10.00"))],
        [("card", Decimal("10.00"))],
    ):
        sales.append(
            await _complete_historical_electronic_sale(
                service,
                scaffold,
                payments=payments,
            )
        )

    pending = await _create_refund_attempt(service, scaffold, *sales[0])
    requires_reconciliation = await _create_refund_attempt(service, scaffold, *sales[1])
    await service.begin_refund_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=requires_reconciliation.id,
        actor_id=scaffold["cashier"].id,
        allowed_branch_ids=None,
    )
    confirmed = await _create_refund_attempt(service, scaffold, *sales[2])
    await service.begin_refund_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=confirmed.id,
        actor_id=scaffold["cashier"].id,
        allowed_branch_ids=None,
    )
    confirmed = await service.confirm_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=confirmed.id,
        actor_id=scaffold["cashier"].id,
        confirmations=[("qr", "QR-TERM", "QR-REFUND-QUEUE")],
        allowed_branch_ids=None,
    )
    await _close_refund_attempts(service, scaffold, sales[3], sales[4])

    actor = CurrentUser(
        user_id=scaffold["cashier"].id,
        tenant_id=scaffold["tenant"].id,
        is_developer=False,
        is_administrator=False,
        permissions={"pos.refund_external_confirm"},
        permission_scopes={
            "pos.refund_external_confirm": frozenset({scaffold["branch"].id}),
        },
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    try:
        response = await client.get("/api/v1/pos/refund-reconciliation")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        body = response.json()
        assert body["total"] == 3
        assert body["page"] == 1
        assert body["page_size"] == 25
        assert {item["status"] for item in body["items"]} == {
            "pending",
            "requires_reconciliation",
            "confirmed",
        }
        assert all("operation_id" not in item for item in body["items"])
        assert {item["id"] for item in body["items"]} == {
            str(pending.id),
            str(requires_reconciliation.id),
            str(confirmed.id),
        }
        pending_item = next(item for item in body["items"] if item["status"] == "pending")
        assert pending_item["parent_receipt_number"] == sales[0][0].receipt_number
        assert pending_item["branch_name"] == scaffold["branch"].name
        assert pending_item["register_name"] == scaffold["register"].name
        assert pending_item["requested_by_name"] == scaffold["cashier"].full_name
        assert pending_item["item_count"] == 1
        assert set(pending_item["payment_methods"]) == {"card", "qr", "bank_transfer"}
        assert body["summary"] == {
            "pending_count": 1,
            "pending_external_amount": "10.00",
            "requires_reconciliation_count": 1,
            "requires_reconciliation_external_amount": "10.00",
            "confirmed_count": 1,
            "confirmed_external_amount": "10.00",
        }
        assert body["branches"] == [
            {"id": str(scaffold["branch"].id), "name": scaffold["branch"].name}
        ]

        filtered = await client.get(
            "/api/v1/pos/refund-reconciliation",
            params={"status": "confirmed", "page_size": 100},
        )
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1
        assert filtered.json()["items"][0]["id"] == str(confirmed.id)
        assert filtered.json()["summary"] == body["summary"]

        assert (
            await client.get(
                "/api/v1/pos/refund-reconciliation",
                params={"page_size": 101},
            )
        ).status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)


async def test_refund_reconciliation_queue_enforces_tenant_branch_and_permission(
    client: AsyncClient,
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold(batch_qty=10, sale_price=10)
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )
    parent, item = await _complete_historical_electronic_sale(service, scaffold)
    visible_attempt = await _create_refund_attempt(service, scaffold, parent, item)

    foundation = FoundationService(FoundationRepository(db_session))
    other_branch = await foundation.create_branch(
        tenant_id=scaffold["tenant"].id,
        fields={"name": "Other branch"},
    )
    other_register = await foundation.create_register(
        tenant_id=scaffold["tenant"].id,
        fields={"branch_id": other_branch.id, "name": "Касса 2"},
    )
    inventory = InventoryRepository(db_session)
    other_batch = await inventory.create_batch(
        tenant_id=scaffold["tenant"].id,
        branch_id=other_branch.id,
        catalog_id=scaffold["item"].id,
        expires_at=scaffold["batch"].expires_at,
        purchase_price=Decimal("3.00"),
        sale_price=Decimal("10.00"),
        qty_initial=Decimal("10.000"),
        qty_remaining=Decimal("0"),
    )
    await inventory.insert_movement(
        tenant_id=scaffold["tenant"].id,
        batch_id=other_batch.id,
        movement_type="incoming",
        qty_delta=Decimal("10.000"),
        source_table=None,
        source_id=None,
    )
    await db_session.refresh(other_batch)
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=other_register.id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )
    hidden_parent, hidden_item = await _complete_historical_electronic_sale(
        service,
        scaffold,
        register_id=other_register.id,
    )
    await _create_refund_attempt(service, scaffold, hidden_parent, hidden_item)

    other_tenant = await pos_scaffold(batch_qty=4, sale_price=10)
    other_service = POSService(POSRepository(db_session))
    await other_service.open_shift(
        tenant_id=other_tenant["tenant"].id,
        register_id=other_tenant["register"].id,
        opened_by_user_id=other_tenant["cashier"].id,
        opening_cash=Decimal("0"),
    )
    other_parent, other_item = await _complete_historical_electronic_sale(
        other_service,
        other_tenant,
    )
    await _create_refund_attempt(other_service, other_tenant, other_parent, other_item)

    actor = CurrentUser(
        user_id=scaffold["cashier"].id,
        tenant_id=scaffold["tenant"].id,
        is_developer=False,
        is_administrator=False,
        permissions={"pos.refund_external_confirm"},
        permission_scopes={
            "pos.refund_external_confirm": frozenset({scaffold["branch"].id}),
        },
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    try:
        response = await client.get("/api/v1/pos/refund-reconciliation")
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == str(visible_attempt.id)
        assert response.json()["branches"] == [
            {"id": str(scaffold["branch"].id), "name": scaffold["branch"].name}
        ]
        forbidden_branch = await client.get(
            "/api/v1/pos/refund-reconciliation",
            params={"branch_id": str(other_branch.id)},
        )
        assert forbidden_branch.status_code == 403
        assert (
            await client.get(f"/api/v1/sales/{hidden_parent.id}/refund-attempts/active")
        ).status_code == 403
        cross_tenant = await client.get(f"/api/v1/sales/{other_parent.id}/refund-attempts/active")
        assert cross_tenant.status_code == 200
        assert cross_tenant.json() is None

        denied_actor = replace(
            actor,
            permissions={"pos.refund"},
            permission_scopes={
                "pos.refund": frozenset({scaffold["branch"].id}),
            },
        )

        async def _override_denied_user() -> CurrentUser:
            return denied_actor

        app.dependency_overrides[current_user] = _override_denied_user
        assert (await client.get("/api/v1/pos/refund-reconciliation")).status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)


async def test_active_refund_attempt_endpoint_recovers_without_client_state(
    client: AsyncClient,
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    service, scaffold, parent, item = await _open_shift_and_sell(
        db_session,
        pos_scaffold,
        qty=2,
        payments=[("card", Decimal("20.00"))],
    )
    attempt = await _create_refund_attempt(service, scaffold, parent, item)
    actor = CurrentUser(
        user_id=scaffold["cashier"].id,
        tenant_id=scaffold["tenant"].id,
        is_developer=False,
        is_administrator=False,
        permissions={"pos.refund"},
        permission_scopes={"pos.refund": frozenset({scaffold["branch"].id})},
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    try:
        recovered = await client.get(f"/api/v1/sales/{parent.id}/refund-attempts/active")
        assert recovered.status_code == 200
        assert recovered.headers["cache-control"] == "private, no-store"
        assert recovered.json()["id"] == str(attempt.id)
        assert recovered.json()["status"] == "pending"

        await service.void_refund_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            reason="customer_cancelled",
            operator_note=None,
            can_manage_tenant=False,
            allowed_branch_ids=None,
            allowed_manage_branch_ids=set(),
        )
        closed = await client.get(f"/api/v1/sales/{parent.id}/refund-attempts/active")
        assert closed.status_code == 200
        assert closed.json() is None

        unauthorized = replace(
            actor,
            permissions={"pos.sell"},
            permission_scopes={"pos.sell": frozenset({scaffold["branch"].id})},
        )

        async def _override_unauthorized_user() -> CurrentUser:
            return unauthorized

        app.dependency_overrides[current_user] = _override_unauthorized_user
        assert (
            await client.get(f"/api/v1/sales/{parent.id}/refund-attempts/active")
        ).status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)
