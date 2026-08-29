"""Database access for the append-only customer-return journal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domains.catalog.models import TenantCatalog
from app.domains.customer_returns.models import (
    CustomerReturnDisposition,
    CustomerReturnQuarantineItem,
)
from app.domains.foundation.models import Branch
from app.domains.inventory.models import Batch
from app.domains.pos.models import Sale


@dataclass(frozen=True)
class CustomerReturnRow:
    item: CustomerReturnQuarantineItem
    disposition: CustomerReturnDisposition | None
    branch_name: str
    catalog_name: str
    catalog_form: str | None
    catalog_dosage: str | None
    batch_number: str | None
    expires_at: date
    return_receipt_number: str | None
    parent_receipt_number: str | None


class CustomerReturnsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert_quarantine_item(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        return_sale_id: UUID,
        return_sale_item_id: UUID,
        parent_sale_id: UUID,
        parent_sale_item_id: UUID,
        catalog_id: UUID,
        batch_id: UUID,
        qty: Decimal,
        refund_reason: str | None,
        refund_comment: str | None,
        received_by: UUID,
    ) -> CustomerReturnQuarantineItem:
        item = CustomerReturnQuarantineItem(
            tenant_id=tenant_id,
            branch_id=branch_id,
            return_sale_id=return_sale_id,
            return_sale_item_id=return_sale_item_id,
            parent_sale_id=parent_sale_id,
            parent_sale_item_id=parent_sale_item_id,
            catalog_id=catalog_id,
            batch_id=batch_id,
            qty=qty,
            refund_reason=refund_reason,
            refund_comment=refund_comment,
            received_by=received_by,
        )
        self.session.add(item)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def lock_operation_id(self, *, tenant_id: UUID, operation_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"customer-return:{tenant_id}:{operation_id}"},
        )

    async def get_disposition_by_operation_id(
        self, *, tenant_id: UUID, operation_id: UUID
    ) -> CustomerReturnDisposition | None:
        stmt = select(CustomerReturnDisposition).where(
            CustomerReturnDisposition.tenant_id == tenant_id,
            CustomerReturnDisposition.operation_id == operation_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def lock_item(
        self, *, tenant_id: UUID, item_id: UUID
    ) -> CustomerReturnQuarantineItem | None:
        stmt = (
            select(CustomerReturnQuarantineItem)
            .where(
                CustomerReturnQuarantineItem.tenant_id == tenant_id,
                CustomerReturnQuarantineItem.id == item_id,
            )
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_disposition_for_item(
        self, *, tenant_id: UUID, item_id: UUID
    ) -> CustomerReturnDisposition | None:
        stmt = select(CustomerReturnDisposition).where(
            CustomerReturnDisposition.tenant_id == tenant_id,
            CustomerReturnDisposition.quarantine_item_id == item_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert_disposition(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        quarantine_item_id: UUID,
        operation_id: UUID,
        operation_hash: str,
        decision: str,
        reason: str,
        comment: str | None,
        resolved_by: UUID,
    ) -> CustomerReturnDisposition:
        disposition = CustomerReturnDisposition(
            tenant_id=tenant_id,
            branch_id=branch_id,
            quarantine_item_id=quarantine_item_id,
            operation_id=operation_id,
            operation_hash=operation_hash,
            decision=decision,
            reason=reason,
            comment=comment,
            resolved_by=resolved_by,
        )
        self.session.add(disposition)
        await self.session.flush()
        await self.session.refresh(disposition)
        return disposition

    async def list_items(
        self,
        *,
        tenant_id: UUID,
        status: str | None,
        branch_id: UUID | None,
        search: str | None,
        allowed_branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
        item_id: UUID | None = None,
    ) -> tuple[list[CustomerReturnRow], int, int, int]:
        if allowed_branch_ids == set():
            return [], 0, 0, 0
        return_sale = aliased(Sale, name="customer_return_sale")
        parent_sale = aliased(Sale, name="customer_return_parent_sale")
        disposition_join = and_(
            CustomerReturnDisposition.tenant_id == CustomerReturnQuarantineItem.tenant_id,
            CustomerReturnDisposition.quarantine_item_id == CustomerReturnQuarantineItem.id,
        )
        conditions = [CustomerReturnQuarantineItem.tenant_id == tenant_id]
        if allowed_branch_ids is not None:
            conditions.append(CustomerReturnQuarantineItem.branch_id.in_(allowed_branch_ids))
        if branch_id is not None:
            conditions.append(CustomerReturnQuarantineItem.branch_id == branch_id)
        if item_id is not None:
            conditions.append(CustomerReturnQuarantineItem.id == item_id)
        if search is not None:
            term = f"%{search.strip().lower()}%"
            conditions.append(
                or_(
                    func.lower(TenantCatalog.brand_name).like(term),
                    func.lower(func.coalesce(Batch.batch_number, "")).like(term),
                    func.lower(func.coalesce(return_sale.receipt_number, "")).like(term),
                    func.lower(func.coalesce(parent_sale.receipt_number, "")).like(term),
                )
            )
        base = (
            select(
                CustomerReturnQuarantineItem,
                CustomerReturnDisposition,
                Branch.name,
                TenantCatalog.brand_name,
                TenantCatalog.form,
                TenantCatalog.dosage,
                Batch.batch_number,
                Batch.expires_at,
                return_sale.receipt_number,
                parent_sale.receipt_number,
            )
            .outerjoin(CustomerReturnDisposition, disposition_join)
            .join(
                Branch,
                and_(
                    Branch.tenant_id == CustomerReturnQuarantineItem.tenant_id,
                    Branch.id == CustomerReturnQuarantineItem.branch_id,
                ),
            )
            .join(
                TenantCatalog,
                and_(
                    TenantCatalog.tenant_id == CustomerReturnQuarantineItem.tenant_id,
                    TenantCatalog.id == CustomerReturnQuarantineItem.catalog_id,
                ),
            )
            .join(
                Batch,
                and_(
                    Batch.tenant_id == CustomerReturnQuarantineItem.tenant_id,
                    Batch.id == CustomerReturnQuarantineItem.batch_id,
                ),
            )
            .join(
                return_sale,
                and_(
                    return_sale.tenant_id == CustomerReturnQuarantineItem.tenant_id,
                    return_sale.id == CustomerReturnQuarantineItem.return_sale_id,
                ),
            )
            .join(
                parent_sale,
                and_(
                    parent_sale.tenant_id == CustomerReturnQuarantineItem.tenant_id,
                    parent_sale.id == CustomerReturnQuarantineItem.parent_sale_id,
                ),
            )
            .where(*conditions)
        )
        metrics = (
            select(
                func.count(CustomerReturnQuarantineItem.id).filter(
                    CustomerReturnDisposition.id.is_(None)
                ),
                func.count(CustomerReturnQuarantineItem.id).filter(
                    CustomerReturnDisposition.id.is_not(None)
                ),
            )
            .outerjoin(CustomerReturnDisposition, disposition_join)
            .join(
                TenantCatalog,
                and_(
                    TenantCatalog.tenant_id == CustomerReturnQuarantineItem.tenant_id,
                    TenantCatalog.id == CustomerReturnQuarantineItem.catalog_id,
                ),
            )
            .join(
                Batch,
                and_(
                    Batch.tenant_id == CustomerReturnQuarantineItem.tenant_id,
                    Batch.id == CustomerReturnQuarantineItem.batch_id,
                ),
            )
            .join(
                return_sale,
                and_(
                    return_sale.tenant_id == CustomerReturnQuarantineItem.tenant_id,
                    return_sale.id == CustomerReturnQuarantineItem.return_sale_id,
                ),
            )
            .join(
                parent_sale,
                and_(
                    parent_sale.tenant_id == CustomerReturnQuarantineItem.tenant_id,
                    parent_sale.id == CustomerReturnQuarantineItem.parent_sale_id,
                ),
            )
            .where(*conditions)
        )
        pending, resolved = (await self.session.execute(metrics)).one()
        status_condition = None
        if status == "pending":
            status_condition = CustomerReturnDisposition.id.is_(None)
        elif status == "resolved":
            status_condition = CustomerReturnDisposition.id.is_not(None)
        paginated = base
        if status_condition is not None:
            paginated = paginated.where(status_condition)
        records = (
            await self.session.execute(
                paginated.order_by(CustomerReturnQuarantineItem.received_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        rows = [
            CustomerReturnRow(
                item=row[0],
                disposition=row[1],
                branch_name=row[2],
                catalog_name=row[3],
                catalog_form=row[4],
                catalog_dosage=row[5],
                batch_number=row[6],
                expires_at=row[7],
                return_receipt_number=row[8],
                parent_receipt_number=row[9],
            )
            for row in records
        ]
        pending_count, resolved_count = int(pending), int(resolved)
        if status == "pending":
            total = pending_count
        elif status == "resolved":
            total = resolved_count
        else:
            total = pending_count + resolved_count
        return rows, total, pending_count, resolved_count

    async def get_row(self, *, tenant_id: UUID, item_id: UUID) -> CustomerReturnRow | None:
        for status in ("pending", "resolved"):
            rows, _total, _pending, _resolved = await self.list_items(
                tenant_id=tenant_id,
                status=status,
                branch_id=None,
                search=None,
                allowed_branch_ids=None,
                page=1,
                page_size=1,
                item_id=item_id,
            )
            if rows:
                return rows[0]
        return None
