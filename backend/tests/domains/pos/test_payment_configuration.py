"""Tenant-configurable POS payment method enforcement."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.models import Sale
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import PaymentAdd, SaleCheckoutPayment
from app.domains.pos.service import POSService


async def _set_payment_settings(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    methods: list[str],
    mixed: bool,
) -> None:
    repo = FoundationRepository(session)
    settings = await repo.get_settings(tenant_id)
    assert settings is not None
    await repo.update_settings(
        settings,
        pos_payment_methods=methods,
        pos_mixed_payment_enabled=mixed,
    )


async def _open_sale(service: POSService, scaffold):  # type: ignore[no-untyped-def]
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
    return sale


def test_pos_input_contract_accepts_qr_and_legacy_retry_method() -> None:
    operation_id = uuid4()
    assert (
        PaymentAdd.model_validate(
            {
                "operation_id": operation_id,
                "payment_method": "qr",
                "amount": "10.00",
            }
        ).payment_method
        == "qr"
    )
    assert (
        SaleCheckoutPayment.model_validate(
            {
                "payment_method": "qr",
                "amount": "10.00",
            }
        ).payment_method
        == "qr"
    )

    assert (
        PaymentAdd.model_validate(
            {
                "operation_id": operation_id,
                "payment_method": "bank_transfer",
                "amount": "10.00",
            }
        ).payment_method
        == "bank_transfer"
    )
    assert (
        SaleCheckoutPayment.model_validate(
            {
                "payment_method": "bank_transfer",
                "amount": "10.00",
            }
        ).payment_method
        == "bank_transfer"
    )


async def test_new_legacy_bank_transfer_payment_is_rejected_by_service(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _open_sale(service, scaffold)

    with pytest.raises(BusinessRuleError, match="Unsupported"):
        await service.add_payment(
            sale_id=sale.id,
            payment_method="bank_transfer",
            amount=Decimal("10"),
            operation_id=uuid4(),
        )


async def test_existing_legacy_bank_transfer_payment_can_be_retried(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    repo = POSRepository(db_session)
    service = POSService(repo)
    sale = await _open_sale(service, scaffold)
    operation_id = uuid4()
    operation_hash = service._payment_operation_hash(
        sale_id=sale.id,
        payment_method="bank_transfer",
        amount=Decimal("10"),
        metadata=None,
    )
    existing = await repo.insert_payment(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        payment_method="bank_transfer",
        amount=Decimal("10"),
        operation_id=operation_id,
        operation_hash=operation_hash,
    )

    retried = await service.add_payment(
        sale_id=sale.id,
        payment_method="bank_transfer",
        amount=Decimal("10.00"),
        operation_id=operation_id,
    )

    assert retried.id == existing.id


async def test_checkout_rejects_disabled_payment_before_creating_sale(
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
    await _set_payment_settings(
        db_session,
        tenant_id=scaffold["tenant"].id,
        methods=["cash"],
        mixed=True,
    )
    operation_id = uuid4()

    with pytest.raises(BusinessRuleError, match="disabled"):
        await service.checkout(
            tenant_id=scaffold["tenant"].id,
            register_id=scaffold["register"].id,
            cashier_user_id=scaffold["cashier"].id,
            operation_id=operation_id,
            items=[(scaffold["item"].id, Decimal("1"))],
            payments=[("qr", Decimal("10"), {"external_confirmed": True})],
        )

    count = await db_session.scalar(
        select(func.count()).select_from(Sale).where(Sale.operation_id == operation_id)
    )
    assert count == 0


async def test_checkout_rejects_mixed_payment_when_disabled(
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
    await _set_payment_settings(
        db_session,
        tenant_id=scaffold["tenant"].id,
        methods=["cash", "card"],
        mixed=False,
    )

    with pytest.raises(BusinessRuleError, match="Mixed"):
        await service.checkout(
            tenant_id=scaffold["tenant"].id,
            register_id=scaffold["register"].id,
            cashier_user_id=scaffold["cashier"].id,
            operation_id=uuid4(),
            items=[(scaffold["item"].id, Decimal("1"))],
            payments=[
                ("cash", Decimal("5"), None),
                ("card", Decimal("5"), {"external_confirmed": True}),
            ],
        )


async def test_legacy_add_payment_rejects_disabled_method(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _open_sale(service, scaffold)
    await _set_payment_settings(
        db_session,
        tenant_id=scaffold["tenant"].id,
        methods=["cash"],
        mixed=True,
    )

    with pytest.raises(BusinessRuleError, match="disabled"):
        await service.add_payment(
            sale_id=sale.id,
            payment_method="card",
            amount=Decimal("10"),
            operation_id=uuid4(),
            metadata={"external_confirmed": True},
        )


async def test_legacy_add_payment_rejects_second_method_when_mixed_disabled(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _open_sale(service, scaffold)
    await _set_payment_settings(
        db_session,
        tenant_id=scaffold["tenant"].id,
        methods=["cash", "card"],
        mixed=False,
    )
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("5"),
        operation_id=uuid4(),
    )

    with pytest.raises(BusinessRuleError, match="Mixed"):
        await service.add_payment(
            sale_id=sale.id,
            payment_method="card",
            amount=Decimal("5"),
            operation_id=uuid4(),
            metadata={"external_confirmed": True},
        )


async def test_checkout_retry_ignores_settings_changed_after_completion(
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
    operation_id = uuid4()
    first = await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
        items=[(scaffold["item"].id, Decimal("1"))],
        payments=[("cash", Decimal("10"), None)],
    )
    await _set_payment_settings(
        db_session,
        tenant_id=scaffold["tenant"].id,
        methods=["qr"],
        mixed=False,
    )
    retried = await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
        items=[(scaffold["item"].id, Decimal("1"))],
        payments=[("cash", Decimal("10"), None)],
    )

    assert retried == first
