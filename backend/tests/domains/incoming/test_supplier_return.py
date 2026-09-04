"""Supplier returns preserve stock and source-document invariants."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.audit.models import AuditLog
from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Branch, Tenant
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.inventory.models import BatchMovement
from app.domains.inventory.repository import InventoryRepository
from app.domains.suppliers.models import Supplier, SupplierReturn
from app.domains.suppliers.repository import SuppliersRepository
from app.domains.suppliers.service import SuppliersService

Scaffold = Callable[[], Awaitable[tuple[Tenant, Branch, TenantCatalog, Supplier]]]


async def _accept_doc_with_batch(
    db_session: AsyncSession,
    scaffold: Scaffold,
    qty: int = 10,
) -> tuple[Tenant, Supplier, UUID]:
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
    assert batch_id is not None
    return tenant, supplier, batch_id


async def test_supplier_return_decreases_batch_qty(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    tenant, supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=10)
    service = SuppliersService(SuppliersRepository(db_session))

    sr = await service.create_return(
        operation_id=uuid4(),
        tenant_id=tenant.id,
        supplier_id=supplier.id,
        batch_id=batch_id,
        qty=Decimal("3"),
        reason="quality_issue",
        comment=None,
        source_document_id=None,
        actor_id=None,
    )
    assert sr.qty == Decimal("3")
    assert sr.amount == Decimal("12.00")  # 3 * 4.00

    audit_entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.table_name == "supplier_return",
                AuditLog.record_id == sr.id,
                AuditLog.action == "INSERT",
            )
        )
    ).scalar_one()
    assert audit_entry.new_values is not None
    assert audit_entry.new_values["amount"] == "***"

    batch = await InventoryRepository(db_session).get_batch(batch_id)
    assert batch is not None
    assert batch.qty_remaining == Decimal("7.000")


async def test_supplier_return_blocks_wrong_supplier(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    tenant, _supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=5)
    suppliers_repo = SuppliersRepository(db_session)
    other_supplier = await suppliers_repo.create_supplier(tenant_id=tenant.id, name="Other")
    service = SuppliersService(suppliers_repo)

    with pytest.raises(BusinessRuleError, match="different supplier"):
        await service.create_return(
            operation_id=uuid4(),
            tenant_id=tenant.id,
            supplier_id=other_supplier.id,
            batch_id=batch_id,
            qty=Decimal("1"),
            reason="other",
            comment=None,
            source_document_id=None,
            actor_id=None,
        )


async def test_supplier_return_refs_must_match_tenant(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    tenant, supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=5)
    other_tenant, other_branch, other_item, other_supplier = await scaffold()
    incoming = IncomingService(IncomingRepository(db_session))
    other_doc = await incoming.create_document(
        tenant_id=other_tenant.id,
        fields={
            "branch_id": other_branch.id,
            "supplier_id": other_supplier.id,
            "document_date": date.today(),
        },
    )
    await incoming.add_item(
        other_doc.id,
        fields={
            "catalog_id": other_item.id,
            "expires_at": date.today() + timedelta(days=180),
            "qty": Decimal("1"),
            "purchase_price": Decimal("4.00"),
            "sale_price": Decimal("10.00"),
        },
    )
    await incoming.accept(other_doc.id)
    service = SuppliersService(SuppliersRepository(db_session))

    with pytest.raises(NotFoundError, match="Supplier not found"):
        await service.create_return(
            operation_id=uuid4(),
            tenant_id=tenant.id,
            supplier_id=other_supplier.id,
            batch_id=batch_id,
            qty=Decimal("1"),
            reason="other",
            comment=None,
            source_document_id=None,
            actor_id=None,
        )

    with pytest.raises(
        BusinessRuleError,
        match="Source document does not match the selected batch",
    ):
        await service.create_return(
            operation_id=uuid4(),
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            batch_id=batch_id,
            qty=Decimal("1"),
            reason="other",
            comment=None,
            source_document_id=other_doc.id,
            actor_id=None,
        )


async def test_supplier_return_overdraw_blocked(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    tenant, supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=2)
    service = SuppliersService(SuppliersRepository(db_session))

    with pytest.raises(BusinessRuleError):
        await service.create_return(
            operation_id=uuid4(),
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            batch_id=batch_id,
            qty=Decimal("99"),
            reason="quality_issue",
            comment=None,
            source_document_id=None,
            actor_id=None,
        )


async def test_supplier_return_retry_is_idempotent(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    tenant, supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=5)
    service = SuppliersService(SuppliersRepository(db_session))
    operation_id = uuid4()

    first = await service.create_return(
        operation_id=operation_id,
        tenant_id=tenant.id,
        supplier_id=supplier.id,
        batch_id=batch_id,
        qty=Decimal("2"),
        reason="damaged",
        comment="Повреждена упаковка",
        source_document_id=None,
        actor_id=None,
    )
    repeated = await service.create_return(
        operation_id=operation_id,
        tenant_id=tenant.id,
        supplier_id=supplier.id,
        batch_id=batch_id,
        qty=Decimal("2"),
        reason="damaged",
        comment="Повреждена упаковка",
        source_document_id=None,
        actor_id=None,
    )

    assert repeated.id == first.id == operation_id
    batch = await InventoryRepository(db_session).get_batch(batch_id, tenant_id=tenant.id)
    assert batch is not None
    assert batch.qty_remaining == Decimal("3.000")
    return_count = int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(SupplierReturn)
                .where(SupplierReturn.id == operation_id)
            )
        ).scalar_one()
    )
    movement_count = int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(BatchMovement)
                .where(BatchMovement.operation_key == f"suppliers:return:{operation_id}")
            )
        ).scalar_one()
    )
    assert return_count == 1
    assert movement_count == 1

    revoked_scope = {uuid4()}
    with pytest.raises(PermissionDeniedError, match="Branch access denied"):
        await service.create_return(
            operation_id=operation_id,
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            batch_id=batch_id,
            qty=Decimal("2"),
            reason="damaged",
            comment="Повреждена упаковка",
            source_document_id=None,
            actor_id=None,
            allowed_branch_ids=revoked_scope,
        )

    # Check the current scope before payload comparison so a caller cannot use
    # validation differences to discover details of a return from another branch.
    with pytest.raises(PermissionDeniedError, match="Branch access denied"):
        await service.create_return(
            operation_id=operation_id,
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            batch_id=batch_id,
            qty=Decimal("1"),
            reason="damaged",
            comment="Повреждена упаковка",
            source_document_id=None,
            actor_id=None,
            allowed_branch_ids=revoked_scope,
        )

    with pytest.raises(BusinessRuleError, match="reused with different data"):
        await service.create_return(
            operation_id=operation_id,
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            batch_id=batch_id,
            qty=Decimal("1"),
            reason="damaged",
            comment="Повреждена упаковка",
            source_document_id=None,
            actor_id=None,
        )


async def test_supplier_return_rejects_inactive_supplier(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    tenant, supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=5)
    service = SuppliersService(SuppliersRepository(db_session))
    await service.update_supplier(
        supplier.id,
        tenant_id=tenant.id,
        fields={"is_active": False},
    )

    with pytest.raises(BusinessRuleError, match="Inactive supplier"):
        await service.create_return(
            operation_id=uuid4(),
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            batch_id=batch_id,
            qty=Decimal("1"),
            reason="damaged",
            comment=None,
            source_document_id=None,
            actor_id=None,
        )

    batch = await InventoryRepository(db_session).get_batch(batch_id, tenant_id=tenant.id)
    assert batch is not None
    assert batch.qty_remaining == Decimal("5.000")


async def test_supplier_return_candidates_history_and_branch_scope(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    tenant, supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=6)
    service = SuppliersService(SuppliersRepository(db_session))
    batch = await InventoryRepository(db_session).get_batch(batch_id, tenant_id=tenant.id)
    assert batch is not None

    candidates, candidate_total = await service.search_return_candidates(
        tenant_id=tenant.id,
        supplier_id=supplier.id,
        branch_id=batch.branch_id,
        branch_ids={batch.branch_id},
        q="Aspirin",
        page=1,
        page_size=20,
    )
    assert candidate_total == 1
    assert candidates[0].batch.id == batch_id

    with pytest.raises(PermissionDeniedError, match="Branch access denied"):
        await service.create_return(
            operation_id=uuid4(),
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            batch_id=batch_id,
            qty=Decimal("1"),
            reason="other",
            comment=None,
            source_document_id=candidates[0].source_document_id,
            actor_id=None,
            allowed_branch_ids={uuid4()},
        )

    await service.create_return(
        operation_id=uuid4(),
        tenant_id=tenant.id,
        supplier_id=supplier.id,
        batch_id=batch_id,
        qty=Decimal("1.5"),
        reason="incorrect_delivery",
        comment="Лишняя позиция",
        source_document_id=candidates[0].source_document_id,
        actor_id=None,
        allowed_branch_ids={batch.branch_id},
    )
    rows, summary, timezone_name = await service.search_returns(
        tenant_id=tenant.id,
        supplier_id=supplier.id,
        branch_id=batch.branch_id,
        branch_ids={batch.branch_id},
        reason="incorrect_delivery",
        date_from=None,
        date_to=None,
        page=1,
        page_size=10,
    )
    assert timezone_name == "Asia/Dushanbe"
    assert summary.total == 1
    assert summary.total_qty == Decimal("1.500")
    assert summary.total_amount == Decimal("6.00")
    assert rows[0].branch_id == batch.branch_id
    assert rows[0].catalog_name == "Aspirin"


async def test_supplier_provenance_rejects_orphan_and_duplicate_movements(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    tenant, _supplier, batch_id = await _accept_doc_with_batch(db_session, scaffold, qty=6)
    source_id = (
        await db_session.execute(
            select(BatchMovement.source_id).where(
                BatchMovement.tenant_id == tenant.id,
                BatchMovement.batch_id == batch_id,
                BatchMovement.source_table == "incoming_item",
            )
        )
    ).scalar_one()
    assert source_id is not None

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                text("""
                    INSERT INTO batch_movement (
                      tenant_id, batch_id, movement_type, qty_delta,
                      source_table, source_id, operation_key
                    ) VALUES (
                      :tenant_id, :batch_id, 'correction', 1,
                      'incoming_item', :source_id, :operation_key
                    )
                    """),
                {
                    "tenant_id": tenant.id,
                    "batch_id": batch_id,
                    "source_id": source_id,
                    "operation_key": f"test:duplicate:{uuid4()}",
                },
            )

    with pytest.raises(DBAPIError, match="inconsistent source provenance"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("""
                    INSERT INTO batch_movement (
                      tenant_id, batch_id, movement_type, qty_delta,
                      source_table, source_id, operation_key
                    ) VALUES (
                      :tenant_id, :batch_id, 'correction', 1,
                      'incoming_item', :source_id, :operation_key
                    )
                    """),
                {
                    "tenant_id": tenant.id,
                    "batch_id": batch_id,
                    "source_id": uuid4(),
                    "operation_key": f"test:orphan:{uuid4()}",
                },
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
