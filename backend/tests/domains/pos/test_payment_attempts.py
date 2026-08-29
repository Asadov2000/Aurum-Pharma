"""Server-trusted card and QR payment attempt invariants."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domains.auth.models import AppUser
from app.domains.pos.models import POSPaymentAttempt
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import POSPaymentAttemptConfirm, POSPaymentAttemptVoid
from app.domains.pos.service import POSService
from app.domains.sync.models import SyncOutboxEvent
from app.domains.sync.repository import SyncOutboxRepository
from app.main import app


async def _draft_sale(
    service: POSService,
    scaffold,  # type: ignore[no-untyped-def]
):  # type: ignore[no-untyped-def]
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    await service.add_item(
        sale_id=sale.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
        actor_id=scaffold["cashier"].id,
    )
    return sale


async def _confirmed_attempt(
    service: POSService,
    scaffold,  # type: ignore[no-untyped-def]
    *,
    sale_id: UUID,
    payment_method: str = "card",
    terminal_id: str = "TERM-01",
    external_reference: str = "TERM-123",
) -> POSPaymentAttempt:
    attempt = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale_id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method=payment_method,
        amount=Decimal("10.00"),
        currency="TJS",
    )
    await service.begin_payment_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
    )
    return await service.confirm_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        terminal_id=terminal_id,
        external_reference=external_reference,
    )


async def test_confirmed_attempt_is_consumed_by_atomic_checkout_and_outbox(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    attempt = await _confirmed_attempt(service, scaffold, sale_id=sale.id)

    result = await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        draft_sale_id=sale.id,
        items=[(scaffold["item"].id, Decimal("1"))],
        payments=[("card", Decimal("10.00"), None, attempt.id)],
    )

    await db_session.refresh(attempt)
    assert attempt.status == "consumed"
    assert attempt.consumed_at is not None
    assert result.payments[0].payment_attempt_id == attempt.id
    assert result.payments[0].payment_attempt_status == "consumed"
    event = await SyncOutboxRepository(db_session).get_by_operation_id(
        tenant_id=scaffold["tenant"].id,
        operation_id=result.operation_id,
    )
    assert event is not None
    assert event.payload["payments"][0]["payment_attempt_id"] == str(attempt.id)
    assert event.payload["payments"][0]["payment_attempt_status"] == "consumed"
    assert "terminal_id" not in event.payload["payments"][0]
    assert "external_reference" not in event.payload["payments"][0]
    assert "metadata" not in event.payload["payments"][0]


async def test_attempt_create_is_idempotent_and_conflicts_on_changed_payload(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    operation_id = uuid4()

    first = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=operation_id,
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )
    retried = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=operation_id,
        payment_method="card",
        amount=Decimal("10.0"),
        currency="TJS",
    )
    assert retried.id == first.id

    with pytest.raises(ConflictError, match="another payment attempt"):
        await service.create_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            sale_id=sale.id,
            actor_id=scaffold["cashier"].id,
            operation_id=operation_id,
            payment_method="qr",
            amount=Decimal("10.00"),
            currency="TJS",
        )


async def test_database_rejects_new_attempt_that_disables_evidence(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    operation_id = uuid4()
    operation_hash = service._payment_attempt_operation_hash(
        sale_id=sale.id,
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )

    with pytest.raises(DBAPIError, match="must start pending without terminal evidence"):
        async with db_session.begin_nested():
            await service.repo.insert_payment_attempt(
                tenant_id=scaffold["tenant"].id,
                sale_id=sale.id,
                cashier_user_id=scaffold["cashier"].id,
                operation_id=operation_id,
                operation_hash=operation_hash,
                payment_method="card",
                amount=Decimal("10.00"),
                currency="TJS",
                status="pending",
                evidence_required=False,
                created_by=scaffold["cashier"].id,
            )


async def test_operation_namespace_blocks_cross_type_reuse_in_service_and_database(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    repo = POSRepository(db_session)
    service = POSService(repo)
    sale = await _draft_sale(service, scaffold)
    operation_id = uuid4()
    await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=operation_id,
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )

    with pytest.raises(ConflictError, match="another POS operation"):
        await service.add_payment(
            sale_id=sale.id,
            payment_method="cash",
            amount=Decimal("10.00"),
            operation_id=operation_id,
        )

    with pytest.raises(IntegrityError, match="already owned by another operation"):
        async with db_session.begin_nested():
            await repo.insert_payment(
                tenant_id=scaffold["tenant"].id,
                sale_id=sale.id,
                payment_method="cash",
                amount=Decimal("10.00"),
                operation_id=operation_id,
                operation_hash="0" * 64,
            )


async def test_operation_namespace_is_tenant_scoped(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    first_scaffold = await pos_scaffold()
    second_scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    first_sale = await _draft_sale(service, first_scaffold)
    second_sale = await _draft_sale(service, second_scaffold)
    operation_id = uuid4()

    first = await service.create_payment_attempt(
        tenant_id=first_scaffold["tenant"].id,
        sale_id=first_sale.id,
        actor_id=first_scaffold["cashier"].id,
        operation_id=operation_id,
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )
    second = await service.create_payment_attempt(
        tenant_id=second_scaffold["tenant"].id,
        sale_id=second_sale.id,
        actor_id=second_scaffold["cashier"].id,
        operation_id=operation_id,
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )

    assert first.tenant_id != second.tenant_id
    assert first.operation_id == second.operation_id == operation_id


async def test_payment_attempt_marks_external_effect_before_terminal_confirmation(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    attempt = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )

    with pytest.raises(BusinessRuleError, match="must start reconciliation"):
        await service.confirm_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            terminal_id="TERM-01",
            external_reference="TERM-BYPASS",
        )

    started = await service.begin_payment_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
    )
    retried = await service.begin_payment_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
    )

    assert started.status == "requires_reconciliation"
    assert retried.id == started.id
    with pytest.raises(BusinessRuleError, match="Resolve the sale's card or QR"):
        await service.complete(sale_id=sale.id, actor_id=scaffold["cashier"].id)

    confirmed = await service.confirm_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        terminal_id="TERM-01",
        external_reference="TERM-RECONCILED",
    )
    assert confirmed.status == "confirmed"


async def test_consumed_attempt_cannot_be_reused_or_voided(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    attempt = await _confirmed_attempt(service, scaffold, sale_id=sale.id)
    await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        draft_sale_id=sale.id,
        items=[(scaffold["item"].id, Decimal("1"))],
        payments=[("card", Decimal("10.00"), None, attempt.id)],
    )

    with pytest.raises(BusinessRuleError, match="requires a refund"):
        await service.void_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            reason="checkout_failed",
            operator_note=None,
        )

    second_sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    await service.add_item(
        sale_id=second_sale.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
        actor_id=scaffold["cashier"].id,
    )
    with pytest.raises(BusinessRuleError, match="does not match checkout"):
        async with db_session.begin_nested():
            await service.checkout(
                tenant_id=scaffold["tenant"].id,
                register_id=scaffold["register"].id,
                cashier_user_id=scaffold["cashier"].id,
                operation_id=uuid4(),
                draft_sale_id=second_sale.id,
                items=[(scaffold["item"].id, Decimal("1"))],
                payments=[("card", Decimal("10.00"), None, attempt.id)],
            )


async def test_confirmed_attempt_cannot_be_voided_and_confirmation_is_idempotent(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    attempt = await _confirmed_attempt(service, scaffold, sale_id=sale.id)

    retried = await service.confirm_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        terminal_id="TERM-01",
        external_reference="TERM-123",
    )
    assert retried.id == attempt.id

    with pytest.raises(ConflictError, match="another reference"):
        await service.confirm_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            terminal_id="TERM-02",
            external_reference="TERM-999",
        )
    with pytest.raises(BusinessRuleError, match="checkout or a refund"):
        await service.void_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            reason="manager_override",
            operator_note=None,
            terminal_id="TERM-01",
            external_reference="TERM-123",
        )


async def test_reconciliation_void_requires_terminal_evidence(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    attempt = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )
    started = await service.begin_payment_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
    )
    started_at = started.reconciliation_started_at
    assert started_at is not None

    retried_start = await service.begin_payment_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
    )
    assert retried_start.reconciliation_started_at == started_at
    with pytest.raises(PermissionDeniedError, match="sales management permission"):
        await service.void_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            reason="terminal_declined",
            operator_note=None,
            terminal_id="TERM-01",
            external_reference="DECLINED-01",
        )
    with pytest.raises(BusinessRuleError, match="Terminal evidence"):
        await service.void_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            reason="terminal_declined",
            operator_note=None,
            can_manage_tenant=True,
        )

    voided = await service.void_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        reason="terminal_declined",
        operator_note=None,
        terminal_id="TERM-01",
        external_reference="DECLINED-01",
        can_manage_tenant=True,
    )
    assert voided.status == "voided"
    assert voided.resolved_by_user_id == scaffold["cashier"].id
    with pytest.raises(PermissionDeniedError, match="sales management permission"):
        await service.void_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            reason="terminal_declined",
            operator_note=None,
            terminal_id="TERM-01",
            external_reference="DECLINED-01",
        )
    retried = await service.void_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        reason="terminal_declined",
        operator_note=None,
        terminal_id="TERM-01",
        external_reference="DECLINED-01",
        can_manage_tenant=True,
    )
    assert retried.id == voided.id


async def test_terminal_reference_is_unique_per_tenant(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    first_sale = await _draft_sale(service, scaffold)
    await _confirmed_attempt(
        service,
        scaffold,
        sale_id=first_sale.id,
        terminal_id="TERM-UNIQUE",
        external_reference="DOC-UNIQUE",
    )
    second_sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    await service.add_item(
        sale_id=second_sale.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
        actor_id=scaffold["cashier"].id,
    )
    second = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=second_sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )
    await service.begin_payment_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=second.id,
        actor_id=scaffold["cashier"].id,
    )
    tenant_id = scaffold["tenant"].id
    cashier_id = scaffold["cashier"].id
    second_id = second.id
    with pytest.raises(ConflictError, match="already used"):
        async with db_session.begin_nested():
            await service.confirm_payment_attempt(
                tenant_id=tenant_id,
                attempt_id=second_id,
                actor_id=cashier_id,
                terminal_id="TERM-UNIQUE",
                external_reference="DOC-UNIQUE",
            )

    db_session.expire_all()
    restored = await service.get_payment_attempt(
        tenant_id=tenant_id,
        attempt_id=second_id,
        actor_id=cashier_id,
    )
    assert restored.status == "requires_reconciliation"
    assert restored.terminal_id is None


async def test_terminal_reference_can_be_reused_by_another_tenant(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    first = await pos_scaffold()
    second = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    first_sale = await _draft_sale(service, first)
    second_sale = await _draft_sale(service, second)

    first_attempt = await _confirmed_attempt(
        service,
        first,
        sale_id=first_sale.id,
        terminal_id="TERM-SHARED",
        external_reference="DOC-SHARED",
    )
    second_attempt = await _confirmed_attempt(
        service,
        second,
        sale_id=second_sale.id,
        terminal_id="TERM-SHARED",
        external_reference="DOC-SHARED",
    )

    assert first_attempt.status == "confirmed"
    assert second_attempt.status == "confirmed"
    assert first_attempt.tenant_id != second_attempt.tenant_id


async def test_active_payment_attempt_blocks_cart_changes_and_shift_close(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    item = (await service.repo.list_items(sale.id))[0]
    attempt = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method="qr",
        amount=Decimal("10.00"),
        currency="TJS",
    )

    with pytest.raises(BusinessRuleError, match="before changing the cart"):
        await service.add_item(
            sale_id=sale.id,
            catalog_id=scaffold["item"].id,
            qty=Decimal("1"),
            actor_id=scaffold["cashier"].id,
        )
    with pytest.raises(BusinessRuleError, match="before changing the cart"):
        await service.update_item(
            sale_id=sale.id,
            item_id=item.id,
            qty=Decimal("2"),
            actor_id=scaffold["cashier"].id,
        )
    with pytest.raises(BusinessRuleError, match="before changing the cart"):
        await service.delete_item(
            sale_id=sale.id,
            item_id=item.id,
            actor_id=scaffold["cashier"].id,
        )
    with pytest.raises(BusinessRuleError, match="payment attempt is unresolved"):
        await service.close_shift(
            shift_id=sale.shift_id,
            closing_cash_actual=Decimal("0"),
            closed_by_user_id=scaffold["cashier"].id,
        )

    await service.void_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        reason="cashier_cancelled",
        operator_note=None,
    )
    updated = await service.update_item(
        sale_id=sale.id,
        item_id=item.id,
        qty=Decimal("2"),
        actor_id=scaffold["cashier"].id,
    )
    assert updated.qty == Decimal("2")


async def test_checkout_rollback_leaves_attempt_confirmed(
    db_session: AsyncSession,
    pos_scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    attempt = await _confirmed_attempt(service, scaffold, sale_id=sale.id)
    tenant_id = scaffold["tenant"].id
    cashier_id = scaffold["cashier"].id
    attempt_id = attempt.id

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
                operation_id=uuid4(),
                draft_sale_id=sale.id,
                items=[(scaffold["item"].id, Decimal("1"))],
                payments=[("card", Decimal("10.00"), None, attempt.id)],
            )

    db_session.expire_all()
    restored = await service.get_payment_attempt(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        actor_id=cashier_id,
    )
    assert restored.status == "confirmed"
    assert restored.consumed_at is None


async def test_checkout_requires_every_active_attempt_to_be_resolved(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    attempt = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )

    with pytest.raises(BusinessRuleError, match="pending card or QR"):
        async with db_session.begin_nested():
            await service.checkout(
                tenant_id=scaffold["tenant"].id,
                register_id=scaffold["register"].id,
                cashier_user_id=scaffold["cashier"].id,
                operation_id=uuid4(),
                draft_sale_id=sale.id,
                items=[(scaffold["item"].id, Decimal("1"))],
                payments=[("cash", Decimal("10.00"), None)],
            )

    await service.begin_payment_attempt_reconciliation(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
    )
    await service.confirm_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        terminal_id="TERM-01",
        external_reference="TERM-ACTIVE",
    )
    with pytest.raises(BusinessRuleError, match="Every confirmed"):
        async with db_session.begin_nested():
            await service.checkout(
                tenant_id=scaffold["tenant"].id,
                register_id=scaffold["register"].id,
                cashier_user_id=scaffold["cashier"].id,
                operation_id=uuid4(),
                draft_sale_id=sale.id,
                items=[(scaffold["item"].id, Decimal("1"))],
                payments=[("cash", Decimal("10.00"), None)],
            )


async def test_legacy_flow_cannot_bypass_or_later_confirm_an_attempt(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    attempt = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method="qr",
        amount=Decimal("10.00"),
        currency="TJS",
    )

    with pytest.raises(BusinessRuleError, match="Resolve the sale's"):
        await service.add_payment(
            sale_id=sale.id,
            payment_method="cash",
            amount=Decimal("10.00"),
            actor_id=scaffold["cashier"].id,
        )
    with pytest.raises(BusinessRuleError, match="Resolve the sale's"):
        await service.complete(sale_id=sale.id, actor_id=scaffold["cashier"].id)

    await service.void_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        reason="cashier_cancelled",
        operator_note=None,
    )
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("10.00"),
        actor_id=scaffold["cashier"].id,
    )
    await service.complete(sale_id=sale.id, actor_id=scaffold["cashier"].id)

    with pytest.raises(ConflictError, match="no longer editable"):
        await service.confirm_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            terminal_id="TERM-01",
            external_reference="TERM-CLOSED",
        )


async def test_void_is_terminal_idempotent_and_blocks_confirmation(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    attempt = await service.create_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        actor_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        payment_method="qr",
        amount=Decimal("10.00"),
        currency="TJS",
    )
    first = await service.void_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        reason="customer_cancelled",
        operator_note="Передумал оплачивать",
    )
    retried = await service.void_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        attempt_id=attempt.id,
        actor_id=scaffold["cashier"].id,
        reason="customer_cancelled",
        operator_note="Передумал оплачивать",
    )
    assert retried.id == first.id
    assert first.status == "voided"
    assert first.voided_at is not None

    with pytest.raises(BusinessRuleError, match="must start reconciliation"):
        await service.confirm_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            terminal_id="TERM-01",
            external_reference="TERM-VOIDED",
        )
    with pytest.raises(ConflictError, match="other details"):
        await service.void_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            attempt_id=attempt.id,
            actor_id=scaffold["cashier"].id,
            reason="timeout",
            operator_note=None,
        )


async def test_atomic_checkout_rejects_client_confirmation_without_attempt(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)

    with pytest.raises(BusinessRuleError, match="confirmed payment attempt"):
        async with db_session.begin_nested():
            await service.checkout(
                tenant_id=scaffold["tenant"].id,
                register_id=scaffold["register"].id,
                cashier_user_id=scaffold["cashier"].id,
                operation_id=uuid4(),
                draft_sale_id=sale.id,
                items=[(scaffold["item"].id, Decimal("1"))],
                payments=[("card", Decimal("10.00"), {"external_confirmed": True}, None)],
            )


async def test_legacy_card_payment_allows_only_exact_idempotent_retry(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    repo = POSRepository(db_session)
    service = POSService(repo)
    sale = await _draft_sale(service, scaffold)
    operation_id = uuid4()
    metadata = {"external_confirmed": True, "terminal_id": "legacy-terminal"}
    operation_hash = service._payment_operation_hash(
        sale_id=sale.id,
        payment_method="card",
        amount=Decimal("10.00"),
        metadata=metadata,
    )
    existing = await repo.insert_payment(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        payment_method="card",
        amount=Decimal("10.00"),
        operation_id=operation_id,
        operation_hash=operation_hash,
        metadata_json=metadata,
    )

    retried = await service.add_payment(
        sale_id=sale.id,
        payment_method="card",
        amount=Decimal("10.0"),
        operation_id=operation_id,
        metadata=metadata,
    )
    assert retried.id == existing.id

    with pytest.raises(BusinessRuleError, match="require atomic checkout"):
        await service.add_payment(
            sale_id=sale.id,
            payment_method="qr",
            amount=Decimal("10.00"),
            operation_id=uuid4(),
            metadata={"external_confirmed": True},
        )


def test_void_note_rejects_likely_pii() -> None:
    for note in ("user@example.com", "https://example.com", "+992 900000000"):
        with pytest.raises(ValueError):
            POSPaymentAttemptVoid.model_validate(
                {"reason": "manager_override", "operator_note": note}
            )


def test_payment_evidence_contract_requires_a_complete_clean_pair() -> None:
    for payload in (
        {},
        {"terminal_id": "TERM-01"},
        {"external_reference": "DOC-01"},
        {"terminal_id": "", "external_reference": "DOC-01"},
        {"terminal_id": "TERM-01", "external_reference": "\u0001DOC"},
    ):
        with pytest.raises(ValueError):
            POSPaymentAttemptConfirm.model_validate(payload)

    with pytest.raises(ValueError):
        POSPaymentAttemptVoid.model_validate(
            {
                "reason": "terminal_declined",
                "terminal_id": "TERM-01",
            }
        )


async def test_payment_attempt_api_requires_permission_and_sale_ownership(
    client: AsyncClient,
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    actor = CurrentUser(
        user_id=scaffold["cashier"].id,
        tenant_id=scaffold["tenant"].id,
        is_developer=False,
        is_administrator=False,
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    payload = {
        "operation_id": str(uuid4()),
        "sale_id": str(sale.id),
        "payment_method": "card",
        "amount": "10.00",
        "currency": "TJS",
    }
    try:
        denied = await client.post("/api/v1/pos/payment-attempts", json=payload)
        assert denied.status_code == 403

        actor.permissions = {"pos.sell"}
        actor.permission_scopes = {"pos.sell": frozenset({scaffold["branch"].id})}
        created = await client.post("/api/v1/pos/payment-attempts", json=payload)
        assert created.status_code == 201
        attempt_id = created.json()["id"]
        bypass = await client.post(
            f"/api/v1/pos/payment-attempts/{attempt_id}/confirm",
            json={"terminal_id": "TERM-API", "external_reference": "TERM-BYPASS"},
        )
        assert bypass.status_code == 422
        reconciliation = await client.post(
            f"/api/v1/pos/payment-attempts/{attempt_id}/reconciliation"
        )
        assert reconciliation.status_code == 200
        assert reconciliation.json()["status"] == "requires_reconciliation"
        confirmed = await client.post(
            f"/api/v1/pos/payment-attempts/{attempt_id}/confirm",
            json={
                "terminal_id": "  TERM-API  ",
                "external_reference": "  TERM-API-1  ",
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["terminal_id"] == "TERM-API"
        assert confirmed.json()["external_reference"] == "TERM-API-1"

        other = AppUser(
            email=f"other-{uuid4().hex[:8]}@aurum.tj",
            full_name="Other Cashier",
            home_tenant_id=scaffold["tenant"].id,
            status="active",
        )
        db_session.add(other)
        await db_session.flush()
        actor.user_id = other.id
        hidden = await client.get(f"/api/v1/pos/payment-attempts/{attempt_id}")
        assert hidden.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)


async def test_payment_attempt_service_rejects_cross_tenant_access(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    first = await pos_scaffold()
    second = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, first)
    attempt = await service.create_payment_attempt(
        tenant_id=first["tenant"].id,
        sale_id=sale.id,
        actor_id=first["cashier"].id,
        operation_id=uuid4(),
        payment_method="card",
        amount=Decimal("10.00"),
        currency="TJS",
    )

    with pytest.raises(NotFoundError, match="Payment attempt not found"):
        await service.get_payment_attempt(
            tenant_id=second["tenant"].id,
            attempt_id=attempt.id,
            actor_id=second["cashier"].id,
        )
