"""Idempotency checks for adding POS payments."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid1, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.audit.models import AuditLog
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.models import SalePayment
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import PaymentAdd, PaymentRead
from app.domains.pos.service import POSService


def test_payment_add_requires_uuid4() -> None:
    with pytest.raises(ValidationError):
        PaymentAdd.model_validate(
            {
                "payment_method": "cash",
                "amount": "10.00",
            }
        )

    with pytest.raises(ValidationError):
        PaymentAdd.model_validate(
            {
                "operation_id": str(uuid1()),
                "payment_method": "cash",
                "amount": "10.00",
            }
        )

    operation_id = uuid4()
    payload = PaymentAdd.model_validate(
        {
            "operation_id": str(operation_id),
            "payment_method": "cash",
            "amount": "10.00",
        }
    )
    assert payload.operation_id == operation_id


@pytest.mark.parametrize(
    "payload",
    [
        {
            "operation_id": str(uuid4()),
            "payment_method": "cash",
            "amount": "0.00",
        },
        {
            "operation_id": str(uuid4()),
            "payment_method": "cash",
            "amount": "10.001",
        },
        {
            "operation_id": str(uuid4()),
            "payment_method": "cash",
            "amount": "1000000000000.00",
        },
        {
            "operation_id": str(uuid4()),
            "payment_method": "cash",
            "amount": "10.00",
            "unexpected": True,
        },
    ],
)
def test_payment_add_rejects_invalid_money_and_unknown_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PaymentAdd.model_validate(payload)


async def test_payment_metadata_is_bounded_and_method_specific(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    repo = POSRepository(db_session)
    service = POSService(repo)
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
    )

    invalid: list[tuple[str, dict[str, object] | None]] = [
        ("card", None),
        ("card", {}),
        ("card", {"external_confirmed": False}),
        ("qr", None),
        ("card", {"cash_received": "10.00"}),
        ("cash", {"external_confirmed": True}),
        ("cash", {"cash_received": "9.99"}),
        ("cash", {"cash_received": "10"}),
        ("cash", {"payload": "x" * 4096}),
        ("cash", {"payload": object()}),
    ]
    for method, metadata in invalid:
        with pytest.raises(BusinessRuleError):
            await service.add_payment(
                sale_id=sale.id,
                payment_method=method,
                amount=Decimal("10.00"),
                operation_id=uuid4(),
                metadata=metadata,
            )

    assert await repo.payments_total(sale.id) == Decimal("0")


async def test_payment_audit_redacts_nested_comments_at_rest(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
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
    )
    payment = await service.add_payment(
        sale_id=sale.id,
        payment_method="card",
        amount=Decimal("10.00"),
        operation_id=uuid4(),
        metadata={
            "external_confirmed": True,
            "comment": "customer details",
            "terminal": {"id": "T-1", "comment": "operator note"},
        },
    )

    audit_entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.table_name == "sale_payment",
                AuditLog.record_id == payment.id,
                AuditLog.action == "INSERT",
            )
        )
    ).scalar_one()

    assert audit_entry.new_values is not None
    metadata = audit_entry.new_values["metadata"]
    assert metadata["external_confirmed"] is True
    assert metadata["comment"] == "***"
    assert metadata["terminal"]["comment"] == "***"
    assert metadata["terminal"]["id"] == "T-1"


async def test_payment_retry_returns_existing_for_canonical_payload(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
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
    )
    operation_id = uuid4()

    first = await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("10.00"),
        operation_id=operation_id,
        metadata={"terminal": {"id": "T-1", "sequence": 7}, "approved": True},
    )
    await service.complete(sale_id=sale.id)
    foundation = FoundationRepository(db_session)
    settings = await foundation.get_settings(scaffold["tenant"].id)
    assert settings is not None
    await foundation.update_settings(settings, pos_payment_methods=["card"])
    second = await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("1E+1"),
        operation_id=operation_id,
        metadata={"approved": True, "terminal": {"sequence": 7, "id": "T-1"}},
    )

    assert second.id == first.id
    assert first.operation_id == operation_id
    assert first.operation_hash is not None
    assert len(first.operation_hash) == 64
    assert PaymentRead.model_validate(first).operation_id == operation_id
    count = await db_session.scalar(
        select(func.count()).select_from(SalePayment).where(SalePayment.sale_id == sale.id)
    )
    assert count == 1


async def test_reused_payment_operation_id_rejects_changed_payload(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )
    first_sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    second_sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    for sale in (first_sale, second_sale):
        await service.add_item(
            sale_id=sale.id,
            catalog_id=scaffold["item"].id,
            qty=Decimal("1"),
        )
    operation_id = uuid4()
    metadata = {"terminal_id": "T-1"}
    await service.add_payment(
        sale_id=first_sale.id,
        payment_method="cash",
        amount=Decimal("10"),
        operation_id=operation_id,
        metadata=metadata,
    )

    with pytest.raises(ConflictError):
        await service.add_payment(
            sale_id=first_sale.id,
            payment_method="card",
            amount=Decimal("10"),
            operation_id=operation_id,
            metadata={**metadata, "external_confirmed": True},
        )
    with pytest.raises(ConflictError):
        await service.add_payment(
            sale_id=first_sale.id,
            payment_method="cash",
            amount=Decimal("11"),
            operation_id=operation_id,
            metadata=metadata,
        )
    with pytest.raises(ConflictError):
        await service.add_payment(
            sale_id=first_sale.id,
            payment_method="cash",
            amount=Decimal("10"),
            operation_id=operation_id,
            metadata={"terminal_id": "T-2"},
        )
    with pytest.raises(ConflictError):
        await service.add_payment(
            sale_id=second_sale.id,
            payment_method="cash",
            amount=Decimal("10"),
            operation_id=operation_id,
            metadata=metadata,
        )

    count = await db_session.scalar(
        select(func.count())
        .select_from(SalePayment)
        .where(SalePayment.operation_id == operation_id)
    )
    assert count == 1


async def test_different_operation_ids_cannot_exceed_sale_total(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    repo = POSRepository(db_session)
    service = POSService(repo)
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
    )

    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("6"),
        operation_id=uuid4(),
    )
    with pytest.raises(BusinessRuleError):
        await service.add_payment(
            sale_id=sale.id,
            payment_method="card",
            amount=Decimal("5"),
            operation_id=uuid4(),
            metadata={"external_confirmed": True},
        )

    assert await repo.payments_total(sale.id) == Decimal("6.00")
    await service.add_payment(
        sale_id=sale.id,
        payment_method="card",
        amount=Decimal("4"),
        operation_id=uuid4(),
        metadata={"external_confirmed": True},
    )
    assert await repo.payments_total(sale.id) == sale.total_amount


async def test_payment_operation_id_is_tenant_scoped(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    first_scaffold = await pos_scaffold()
    second_scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    operation_id = uuid4()
    payments: list[SalePayment] = []

    for scaffold in (first_scaffold, second_scaffold):
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
        )
        payments.append(
            await service.add_payment(
                sale_id=sale.id,
                payment_method="cash",
                amount=Decimal("10"),
                operation_id=operation_id,
            )
        )

    assert payments[0].id != payments[1].id
    assert payments[0].tenant_id != payments[1].tenant_id


async def test_payment_and_refund_share_operation_id_namespace(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    repo = POSRepository(db_session)
    service = POSService(repo)
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )
    parent = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    items, _ = await service.add_item(
        sale_id=parent.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
    )
    payment_operation_id = uuid4()
    await service.add_payment(
        sale_id=parent.id,
        payment_method="cash",
        amount=Decimal("10"),
        operation_id=payment_operation_id,
    )
    await service.complete(sale_id=parent.id)

    with pytest.raises(ConflictError):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(items[0].id, Decimal("1"))],
            reason="customer return",
            comment=None,
            cashier_user_id=scaffold["cashier"].id,
            operation_id=payment_operation_id,
        )

    refund_operation_id = uuid4()
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(items[0].id, Decimal("1"))],
        reason="customer return",
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=refund_operation_id,
    )
    next_sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    await service.add_item(
        sale_id=next_sale.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("1"),
    )

    with pytest.raises(ConflictError):
        await service.add_payment(
            sale_id=next_sale.id,
            payment_method="cash",
            amount=Decimal("10"),
            operation_id=refund_operation_id,
        )

    assert (
        await repo.get_payment_by_operation_id(
            tenant_id=scaffold["tenant"].id,
            operation_id=payment_operation_id,
        )
        is not None
    )
    existing_return = await repo.get_sale_by_operation_id(
        tenant_id=scaffold["tenant"].id,
        operation_id=refund_operation_id,
    )
    assert existing_return is not None
    assert existing_return.id == returned.id


async def test_payment_operation_constraints_are_enforced(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    repo = POSRepository(db_session)
    service = POSService(repo)
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
    fields = {
        "tenant_id": scaffold["tenant"].id,
        "sale_id": sale.id,
        "payment_method": "cash",
        "amount": Decimal("1"),
    }

    await repo.insert_payment(**fields)
    await repo.insert_payment(**fields)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await repo.insert_payment(**fields, operation_id=uuid4())

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await repo.insert_payment(
                **fields,
                operation_id=uuid4(),
                operation_hash="not-a-sha256",
            )

    operation_id = uuid4()
    await repo.insert_payment(
        **fields,
        operation_id=operation_id,
        operation_hash="a" * 64,
    )
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await repo.insert_payment(
                **fields,
                operation_id=operation_id,
                operation_hash="b" * 64,
            )
