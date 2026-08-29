"""Business rules for customer-return quarantine and final disposition."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from uuid import UUID

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.customer_returns.models import CustomerReturnDisposition
from app.domains.customer_returns.repository import CustomerReturnRow, CustomerReturnsRepository
from app.domains.customer_returns.schemas import CustomerReturnRead


class CustomerReturnsService:
    def __init__(self, repository: CustomerReturnsRepository) -> None:
        self.repo = repository

    async def record_refund_item(
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
    ) -> None:
        await self.repo.insert_quarantine_item(
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

    async def list_returns(
        self,
        *,
        tenant_id: UUID,
        status: str | None,
        branch_id: UUID | None,
        search: str | None,
        allowed_branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
    ) -> tuple[list[CustomerReturnRead], int, int, int]:
        rows, total, pending, resolved = await self.repo.list_items(
            tenant_id=tenant_id,
            status=status,
            branch_id=branch_id,
            search=search,
            allowed_branch_ids=allowed_branch_ids,
            page=page,
            page_size=page_size,
        )
        return [self._to_read(row) for row in rows], total, pending, resolved

    async def resolve(
        self,
        *,
        tenant_id: UUID,
        item_id: UUID,
        operation_id: UUID,
        disposition_type: str,
        reason_code: str,
        comment: str | None,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> CustomerReturnRead:
        normalized_comment = comment.strip() or None if comment is not None else None
        operation_hash = self._operation_hash(
            item_id=item_id,
            disposition_type=disposition_type,
            reason_code=reason_code,
            comment=normalized_comment,
        )
        await self.repo.lock_operation_id(tenant_id=tenant_id, operation_id=operation_id)
        item = await self.repo.lock_item(tenant_id=tenant_id, item_id=item_id)
        if item is None:
            raise NotFoundError("Customer return not found")
        if allowed_branch_ids is not None and item.branch_id not in allowed_branch_ids:
            raise NotFoundError("Customer return not found")
        existing_operation = await self.repo.get_disposition_by_operation_id(
            tenant_id=tenant_id, operation_id=operation_id
        )
        if existing_operation is not None:
            self._assert_replay(
                existing_operation,
                item_id=item_id,
                operation_hash=operation_hash,
            )
            return await self._read(tenant_id=tenant_id, item_id=item_id)

        existing_disposition = await self.repo.get_disposition_for_item(
            tenant_id=tenant_id, item_id=item_id
        )
        if existing_disposition is not None:
            raise ConflictError("Customer return already has a final disposition")
        await self.repo.insert_disposition(
            tenant_id=tenant_id,
            branch_id=item.branch_id,
            quarantine_item_id=item.id,
            operation_id=operation_id,
            operation_hash=operation_hash,
            decision=disposition_type,
            reason=reason_code,
            comment=normalized_comment,
            resolved_by=actor_id,
        )
        return await self._read(tenant_id=tenant_id, item_id=item_id)

    async def _read(self, *, tenant_id: UUID, item_id: UUID) -> CustomerReturnRead:
        row = await self.repo.get_row(tenant_id=tenant_id, item_id=item_id)
        if row is None:
            raise BusinessRuleError("Customer-return disposition could not be read")
        return self._to_read(row)

    @staticmethod
    def _operation_hash(
        *,
        item_id: UUID,
        disposition_type: str,
        reason_code: str,
        comment: str | None,
    ) -> str:
        payload = {
            "comment": comment,
            "disposition_type": disposition_type,
            "item_id": str(item_id),
            "reason_code": reason_code,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _assert_replay(
        disposition: CustomerReturnDisposition,
        *,
        item_id: UUID,
        operation_hash: str,
    ) -> None:
        if (
            disposition.quarantine_item_id != item_id
            or disposition.operation_hash != operation_hash
        ):
            raise ConflictError("operation_id was already used with a different request")

    @staticmethod
    def _to_read(row: CustomerReturnRow) -> CustomerReturnRead:
        disposition = row.disposition
        return CustomerReturnRead(
            id=row.item.id,
            branch_id=row.item.branch_id,
            branch_name=row.branch_name,
            return_sale_id=row.item.return_sale_id,
            return_receipt_number=row.return_receipt_number,
            parent_sale_id=row.item.parent_sale_id,
            parent_receipt_number=row.parent_receipt_number,
            catalog_id=row.item.catalog_id,
            catalog_name=row.catalog_name,
            catalog_form=row.catalog_form,
            catalog_dosage=row.catalog_dosage,
            batch_id=row.item.batch_id,
            batch_number=row.batch_number,
            expires_at=row.expires_at,
            qty=row.item.qty,
            refund_reason=row.item.refund_reason,
            refund_comment=row.item.refund_comment,
            received_at=row.item.received_at,
            received_by=row.item.received_by,
            status="resolved" if disposition is not None else "pending",
            disposition_type=disposition.decision if disposition is not None else None,
            disposition_reason=disposition.reason if disposition is not None else None,
            disposition_comment=disposition.comment if disposition is not None else None,
            resolved_at=disposition.resolved_at if disposition is not None else None,
            resolved_by=disposition.resolved_by if disposition is not None else None,
        )
