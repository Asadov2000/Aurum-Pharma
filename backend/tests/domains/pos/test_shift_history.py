"""Shift history for reports: filters, pagination, and branch scope."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.models import Shift
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def _closed_shift(
    service: POSService,
    *,
    tenant_id: UUID,
    register_id: UUID,
    cashier_id: UUID,
    opened_at: datetime,
) -> Shift:
    shift = await service.open_shift(
        tenant_id=tenant_id,
        register_id=register_id,
        opened_by_user_id=cashier_id,
        opening_cash=Decimal("100.00"),
    )
    closed = await service.close_shift(
        shift_id=shift.id,
        closing_cash_actual=Decimal("100.00"),
        closed_by_user_id=cashier_id,
    )
    closed.opened_at = opened_at
    await service.repo.session.flush()
    return closed


async def test_shift_history_is_tenant_scoped_resolved_and_paginated(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    first = await pos_scaffold()
    other_tenant = await pos_scaffold()
    service = POSService(POSRepository(db_session))

    oldest = await _closed_shift(
        service,
        tenant_id=first["tenant"].id,
        register_id=first["register"].id,
        cashier_id=first["cashier"].id,
        opened_at=datetime(2026, 5, 1, 5, tzinfo=UTC),
    )
    newest = await _closed_shift(
        service,
        tenant_id=first["tenant"].id,
        register_id=first["register"].id,
        cashier_id=first["cashier"].id,
        opened_at=datetime(2026, 5, 2, 5, tzinfo=UTC),
    )
    await _closed_shift(
        service,
        tenant_id=other_tenant["tenant"].id,
        register_id=other_tenant["register"].id,
        cashier_id=other_tenant["cashier"].id,
        opened_at=datetime(2026, 5, 3, 5, tzinfo=UTC),
    )

    page_one, total = await service.list_shifts(
        tenant_id=first["tenant"].id,
        status="closed",
        branch_id=None,
        register_id=first["register"].id,
        cashier_id=None,
        cashier_query="cash",
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 2),
        allowed_branch_ids=None,
        page=1,
        page_size=1,
    )
    page_two, _ = await service.list_shifts(
        tenant_id=first["tenant"].id,
        status="closed",
        branch_id=None,
        register_id=first["register"].id,
        cashier_id=first["cashier"].id,
        cashier_query=None,
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 2),
        allowed_branch_ids=None,
        page=2,
        page_size=1,
    )

    assert total == 2
    assert page_one[0]["id"] == newest.id
    assert page_two[0]["id"] == oldest.id
    assert page_one[0]["branch_name"] == "Main"
    assert page_one[0]["register_name"] == "Касса 1"
    assert page_one[0]["cashier_name"] == "Cashier"
    assert page_one[0]["sales_total"] == Decimal("0")
    assert page_one[0]["returns_total"] == Decimal("0")
    assert page_one[0]["sales_count"] == 0
    assert page_one[0]["returns_count"] == 0


async def test_shift_history_enforces_capability_branch_scope(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    foundation = FoundationService(FoundationRepository(db_session))
    service = POSService(POSRepository(db_session))
    second_branch = await foundation.create_branch(
        tenant_id=scaffold["tenant"].id,
        fields={"name": "Second"},
    )
    second_register = await foundation.create_register(
        tenant_id=scaffold["tenant"].id,
        fields={"branch_id": second_branch.id, "name": "Касса 2"},
    )

    allowed = await _closed_shift(
        service,
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_id=scaffold["cashier"].id,
        opened_at=datetime(2026, 6, 1, 5, tzinfo=UTC),
    )
    await _closed_shift(
        service,
        tenant_id=scaffold["tenant"].id,
        register_id=second_register.id,
        cashier_id=scaffold["cashier"].id,
        opened_at=datetime(2026, 6, 2, 5, tzinfo=UTC),
    )

    rows, total = await service.list_shifts(
        tenant_id=scaffold["tenant"].id,
        status="closed",
        branch_id=None,
        register_id=None,
        cashier_id=None,
        cashier_query=None,
        date_from=None,
        date_to=None,
        allowed_branch_ids={scaffold["branch"].id},
        page=1,
        page_size=25,
    )

    assert total == 1
    assert [row["id"] for row in rows] == [allowed.id]

    with pytest.raises(PermissionDeniedError, match="Branch access denied"):
        await service.list_shifts(
            tenant_id=scaffold["tenant"].id,
            status="closed",
            branch_id=second_branch.id,
            register_id=None,
            cashier_id=None,
            cashier_query=None,
            date_from=None,
            date_to=None,
            allowed_branch_ids={scaffold["branch"].id},
            page=1,
            page_size=25,
        )


async def test_shift_history_reports_gross_sales_and_returns(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold(sale_price=Decimal("10"), batch_qty=100)
    service = POSService(POSRepository(db_session))
    shift = await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("100"),
    )
    sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    items, _ = await service.add_item(
        sale_id=sale.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("2"),
    )
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("20"),
    )
    await service.complete(sale_id=sale.id)
    await service.refund(
        parent_sale_id=sale.id,
        items=[(items[0].id, Decimal("1"))],
        reason="defect",
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
    )
    await service.close_shift(
        shift_id=shift.id,
        closing_cash_actual=Decimal("110"),
        closed_by_user_id=scaffold["cashier"].id,
    )

    rows, total = await service.list_shifts(
        tenant_id=scaffold["tenant"].id,
        status="closed",
        branch_id=None,
        register_id=None,
        cashier_id=None,
        cashier_query=None,
        date_from=None,
        date_to=None,
        allowed_branch_ids=None,
        page=1,
        page_size=25,
    )

    assert total == 1
    assert rows[0]["id"] == shift.id
    assert rows[0]["sales_total"] == Decimal("20.00")
    assert rows[0]["returns_total"] == Decimal("10.00")
    assert rows[0]["sales_count"] == 1
    assert rows[0]["returns_count"] == 1
