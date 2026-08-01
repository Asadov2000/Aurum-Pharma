"""Shift lifecycle: open, single-open invariant, close + totals."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.audit.models import AuditLog
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def test_open_shift_happy(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold()
    service = POSService(POSRepository(db_session))

    shift = await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("100.00"),
    )
    assert shift.status == "open"
    assert shift.opening_cash == Decimal("100.00")


async def test_cannot_open_two_shifts_on_same_register(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold()
    service = POSService(POSRepository(db_session))

    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    with pytest.raises(ConflictError):
        await service.open_shift(
            tenant_id=s["tenant"].id,
            register_id=s["register"].id,
            opened_by_user_id=s["cashier"].id,
            opening_cash=Decimal("0"),
        )


async def test_manager_can_resume_another_cashiers_open_shift(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    shift = await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("100.00"),
    )
    manager_id = uuid4()

    cashier_view = await service.get_current_shift(
        user_id=manager_id,
        register_id=s["register"].id,
    )
    manager_view = await service.get_current_shift(
        user_id=manager_id,
        register_id=s["register"].id,
        can_manage_tenant=True,
    )

    assert cashier_view is None
    assert manager_view is not None
    assert manager_view.id == shift.id


async def test_open_shift_rejects_register_on_inactive_branch(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold()
    foundation = FoundationService(FoundationRepository(db_session))
    service = POSService(POSRepository(db_session))

    await foundation.create_branch(tenant_id=s["tenant"].id, fields={"name": "Keep active"})
    await foundation.soft_delete_branch(s["branch"].id)

    with pytest.raises(BusinessRuleError, match="Branch is inactive"):
        await service.open_shift(
            tenant_id=s["tenant"].id,
            register_id=s["register"].id,
            opened_by_user_id=s["cashier"].id,
            opening_cash=Decimal("0"),
        )


async def test_close_shift_computes_expected_and_diff(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold()
    service = POSService(POSRepository(db_session))

    shift = await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("50.00"),
    )

    closed = await service.close_shift(
        shift_id=shift.id,
        closing_cash_actual=Decimal("55.00"),
        closed_by_user_id=s["cashier"].id,
    )
    assert closed.status == "closed"
    assert closed.closing_cash_expected == Decimal("50.00")  # no cash sales yet
    assert closed.closing_difference == Decimal("5.00")

    audit_rows = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(
                    AuditLog.table_name == "shift",
                    AuditLog.record_id == shift.id,
                )
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert [row.action for row in audit_rows] == ["INSERT", "UPDATE"]
    assert audit_rows[0].new_values is not None
    assert audit_rows[0].new_values["opening_cash"] == 50.0
    assert audit_rows[1].changed_fields is not None
    assert audit_rows[1].changed_fields["closing_cash_actual"] == 55.0


async def test_close_shift_rejects_unfinished_sale_with_items(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    shift = await service.open_shift(
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
    await service.add_item(
        sale_id=sale.id,
        catalog_id=s["item"].id,
        qty=Decimal("1"),
    )

    with pytest.raises(BusinessRuleError, match="unfinished sales"):
        await service.close_shift(
            shift_id=shift.id,
            closing_cash_actual=Decimal("0"),
            closed_by_user_id=s["cashier"].id,
        )

    await db_session.refresh(shift)
    assert shift.status == "open"
