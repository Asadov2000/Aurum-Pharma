"""Read-only physical quarantine: display historical units, never current pricing."""

from uuid import UUID

from app.domains.customer_returns.physical_repository import (
    PhysicalCustomerReturnRow,
    PhysicalCustomerReturnsRepository,
)
from app.domains.customer_returns.physical_schemas import (
    PhysicalCustomerReturnList,
    PhysicalCustomerReturnRead,
)
from app.domains.pos.unit_documents import UNIT_LABELS


class PhysicalCustomerReturnsService:
    def __init__(self, repository: PhysicalCustomerReturnsRepository) -> None:
        self.repo = repository

    async def list_returns(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID | None,
        search: str | None,
        allowed_branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
    ) -> PhysicalCustomerReturnList:
        rows, total = await self.repo.list_items(
            tenant_id=tenant_id,
            branch_id=branch_id,
            search=search,
            allowed_branch_ids=allowed_branch_ids,
            page=page,
            page_size=page_size,
        )
        return PhysicalCustomerReturnList(
            items=[self._to_read(row) for row in rows], total=total, page=page, page_size=page_size
        )

    @staticmethod
    def _to_read(row: PhysicalCustomerReturnRow) -> PhysicalCustomerReturnRead:
        return PhysicalCustomerReturnRead(
            operation_id=row.operation_id,
            line_id=row.line_id,
            lot_id=row.lot_id,
            branch_id=row.branch_id,
            branch_name=row.branch_name,
            report_timezone=row.report_timezone,
            parent_sale_id=row.parent_sale_id,
            parent_receipt_number=row.parent_receipt_number,
            return_sale_id=row.return_sale_id,
            return_receipt_number=row.return_receipt_number,
            catalog_id=row.catalog_id,
            catalog_name=row.catalog_name,
            base_units=row.base_units,
            unit_label=UNIT_LABELS[row.packaging.unit],
            batch_number=row.batch_number,
            expires_at=row.expires_at,
            received_at=row.received_at,
            refund_reason=row.refund_reason,
            refund_comment=row.refund_comment,
            intake_kind=row.intake_kind,
        )
