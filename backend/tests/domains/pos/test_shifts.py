"""Shift lifecycle: open, single-open invariant, close + totals."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
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
