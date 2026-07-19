"""Incoming-document lifecycle: draft → add items → accept → batches."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, NotFoundError
from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Branch, Tenant
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.inventory.models import Batch
from app.domains.inventory.repository import InventoryRepository
from app.domains.suppliers.models import Supplier

Scaffold = Callable[[], Awaitable[tuple[Tenant, Branch, TenantCatalog, Supplier]]]


async def test_create_incoming_draft(db_session: AsyncSession, scaffold) -> None:
    tenant, branch, _item, supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))

    doc = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    assert doc.status == "draft"
    assert doc.total_amount == Decimal("0.00")


async def test_list_incoming_is_searchable_and_paginated(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    tenant, branch, _item, supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))
    today = date.today()

    for index in range(5):
        await service.create_document(
            tenant_id=tenant.id,
            fields={
                "branch_id": branch.id,
                "supplier_id": supplier.id,
                "document_date": today - timedelta(days=index),
                "document_number": f"ПР-{100 + index}",
            },
        )

    page_rows, total = await service.list_documents(page=2, page_size=2)
    search_rows, search_total = await service.list_documents(
        document_number="пр-103",
        page=1,
        page_size=2,
    )

    assert total == 5
    assert [row.document_number for row in page_rows] == ["ПР-102", "ПР-103"]
    assert search_total == 1
    assert [row.document_number for row in search_rows] == ["ПР-103"]


async def test_incoming_document_refs_must_match_tenant(db_session: AsyncSession, scaffold) -> None:
    tenant, branch, _item, supplier = await scaffold()
    other_tenant, other_branch, _other_item, other_supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))

    with pytest.raises(NotFoundError, match="Branch not found"):
        await service.create_document(
            tenant_id=tenant.id,
            fields={
                "branch_id": other_branch.id,
                "supplier_id": supplier.id,
                "document_date": date.today(),
            },
        )

    with pytest.raises(NotFoundError, match="Supplier not found"):
        await service.create_document(
            tenant_id=tenant.id,
            fields={
                "branch_id": branch.id,
                "supplier_id": other_supplier.id,
                "document_date": date.today(),
            },
        )

    doc = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    with pytest.raises(NotFoundError, match="Branch not found"):
        await service.update_document(doc.id, fields={"branch_id": other_branch.id})
    with pytest.raises(NotFoundError, match="Supplier not found"):
        await service.update_document(doc.id, fields={"supplier_id": other_supplier.id})

    assert other_tenant.id != tenant.id


async def test_add_items_recomputes_total(db_session: AsyncSession, scaffold) -> None:
    tenant, branch, item, supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))

    doc = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    await service.add_item(
        doc.id,
        fields={
            "catalog_id": item.id,
            "expires_at": date.today() + timedelta(days=180),
            "qty": Decimal("10"),
            "purchase_price": Decimal("5.00"),
            "sale_price": Decimal("12.00"),
        },
    )
    await service.add_item(
        doc.id,
        fields={
            "catalog_id": item.id,
            "expires_at": date.today() + timedelta(days=200),
            "qty": Decimal("3"),
            "purchase_price": Decimal("4.50"),
            "sale_price": Decimal("11.00"),
        },
    )

    fresh = await service.get_document(doc.id)
    # 10 * 5.00 + 3 * 4.50 = 50.00 + 13.50 = 63.50
    assert fresh.total_amount == Decimal("63.50")


async def test_incoming_item_catalog_must_match_document_tenant(
    db_session: AsyncSession, scaffold
) -> None:
    tenant, branch, item, supplier = await scaffold()
    _other_tenant, _other_branch, other_item, _other_supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))

    doc = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )

    with pytest.raises(NotFoundError, match="Catalog item not found"):
        await service.add_item(
            doc.id,
            fields={
                "catalog_id": other_item.id,
                "expires_at": date.today() + timedelta(days=180),
                "qty": Decimal("10"),
                "purchase_price": Decimal("5.00"),
                "sale_price": Decimal("12.00"),
            },
        )

    created = await service.add_item(
        doc.id,
        fields={
            "catalog_id": item.id,
            "expires_at": date.today() + timedelta(days=180),
            "qty": Decimal("10"),
            "purchase_price": Decimal("5.00"),
            "sale_price": Decimal("12.00"),
        },
    )
    with pytest.raises(NotFoundError, match="Catalog item not found"):
        await service.update_item(doc.id, created.id, fields={"catalog_id": other_item.id})


async def test_accept_creates_batches_with_correct_qty(db_session: AsyncSession, scaffold) -> None:
    tenant, branch, item, supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))

    doc = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    await service.add_item(
        doc.id,
        fields={
            "catalog_id": item.id,
            "expires_at": date.today() + timedelta(days=180),
            "qty": Decimal("25"),
            "purchase_price": Decimal("3.00"),
            "sale_price": Decimal("9.00"),
        },
    )
    accepted = await service.accept(doc.id)
    assert accepted.status == "accepted"
    assert accepted.accepted_at is not None

    # Item now has created_batch_id, and the batch has qty_remaining=25.
    items = await service.list_items(doc.id)
    assert items[0].created_batch_id is not None

    batch = await InventoryRepository(db_session).get_batch(items[0].created_batch_id)
    assert batch is not None
    assert batch.qty_remaining == Decimal("25.000")
    assert batch.qty_initial == Decimal("25.000")


async def test_cannot_edit_accepted_document(db_session: AsyncSession, scaffold) -> None:
    tenant, branch, item, supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))

    doc = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    await service.add_item(
        doc.id,
        fields={
            "catalog_id": item.id,
            "expires_at": date.today() + timedelta(days=90),
            "qty": Decimal("1"),
            "purchase_price": Decimal("1"),
            "sale_price": Decimal("1"),
        },
    )
    await service.accept(doc.id)

    with pytest.raises(BusinessRuleError):
        await service.update_document(doc.id, fields={"notes": "no"})
    with pytest.raises(BusinessRuleError):
        await service.add_item(
            doc.id,
            fields={
                "catalog_id": item.id,
                "expires_at": date.today() + timedelta(days=90),
                "qty": Decimal("1"),
                "purchase_price": Decimal("1"),
                "sale_price": Decimal("1"),
            },
        )


async def test_accept_with_past_expiry_blocked(db_session: AsyncSession, scaffold) -> None:
    tenant, branch, item, supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))

    doc = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    await service.add_item(
        doc.id,
        fields={
            "catalog_id": item.id,
            "expires_at": date.today() - timedelta(days=1),  # expired
            "qty": Decimal("1"),
            "purchase_price": Decimal("1"),
            "sale_price": Decimal("1"),
        },
    )
    with pytest.raises(BusinessRuleError):
        await service.accept(doc.id)


async def test_reject_locks_document_without_batches(db_session: AsyncSession, scaffold) -> None:
    tenant, branch, item, supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))

    doc = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    await service.add_item(
        doc.id,
        fields={
            "catalog_id": item.id,
            "expires_at": date.today() + timedelta(days=180),
            "qty": Decimal("5"),
            "purchase_price": Decimal("1"),
            "sale_price": Decimal("1"),
        },
    )
    rejected = await service.reject(doc.id)
    assert rejected.status == "rejected"

    # No batches were created for this document.
    rows = (
        (await db_session.execute(select(Batch).where(Batch.tenant_id == tenant.id)))
        .scalars()
        .all()
    )
    assert rows == []
