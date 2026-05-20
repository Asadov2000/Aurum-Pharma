"""supplier_return: decreases batch qty and surfaces a warning on
cross-supplier returns."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.inventory.repository import InventoryRepository
from app.domains.suppliers.repository import SuppliersRepository
from app.domains.suppliers.service import SuppliersService


async def _accept_doc_with_batch(db_session, scaffold, qty: int = 10):
    tenant, branch, item, supplier = await scaffold()
    incoming = IncomingService(IncomingRepository(db_session))
    doc = await incoming.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    await incoming.add_item(
        doc.id,
        fields={
            "catalog_id": item.id,
            "expires_at": date.today() + timedelta(days=180),
            "qty": Decimal(qty),
            "purchase_price": Decimal("4.00"),
            "sale_price": Decimal("10.00"),
        },
    )
    await incoming.accept(doc.id)
    items = await incoming.list_items(doc.id)
    batch_id = items[0].created_batch_id
    return tenant, supplier, batch_id


async def test_supplier_return_decreases_batch_qty(db_session: AsyncSession, scaffold) -> None:
    tenant, supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=10)
    service = SuppliersService(SuppliersRepository(db_session))

    sr, warning = await service.create_return(
        tenant_id=tenant.id,
        supplier_id=supplier.id,
        batch_id=batch_id,
        qty=Decimal("3"),
        reason="defective",
        comment=None,
        source_document_id=None,
        actor_id=None,
    )
    assert sr.qty == Decimal("3")
    assert sr.amount == Decimal("12.00")  # 3 * 4.00
    assert warning is None

    batch = await InventoryRepository(db_session).get_batch(batch_id)
    assert batch is not None
    assert batch.qty_remaining == Decimal("7.000")


async def test_supplier_return_warns_on_wrong_supplier(db_session: AsyncSession, scaffold) -> None:
    """Return the batch to a *different* supplier than the one who delivered
    it. The service still records the return but flags a warning."""
    tenant, _supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=5)
    suppliers_repo = SuppliersRepository(db_session)
    other_supplier = await suppliers_repo.create_supplier(tenant_id=tenant.id, name="Other")
    service = SuppliersService(suppliers_repo)

    sr, warning = await service.create_return(
        tenant_id=tenant.id,
        supplier_id=other_supplier.id,
        batch_id=batch_id,
        qty=Decimal("1"),
        reason="other",
        comment=None,
        source_document_id=None,
        actor_id=None,
    )
    assert sr.supplier_id == other_supplier.id
    assert warning is not None
    assert "different supplier" in warning.lower()


async def test_supplier_return_overdraw_blocked(db_session: AsyncSession, scaffold) -> None:
    tenant, supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=2)
    service = SuppliersService(SuppliersRepository(db_session))

    with pytest.raises(BusinessRuleError):
        await service.create_return(
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            batch_id=batch_id,
            qty=Decimal("99"),
            reason="defective",
            comment=None,
            source_document_id=None,
            actor_id=None,
        )
