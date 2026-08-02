"""Business logic for suppliers and supplier returns."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError

import structlog
from sqlalchemy.exc import IntegrityError, InternalError

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.time import local_day_range
from app.domains.foundation.repository import FoundationRepository
from app.domains.inventory.repository import InventoryRepository
from app.domains.suppliers.models import Supplier, SupplierReturn
from app.domains.suppliers.repository import (
    SupplierReturnCandidateRow,
    SupplierReturnRow,
    SupplierReturnSummaryData,
    SupplierSearchSummaryData,
    SuppliersRepository,
)

logger = structlog.get_logger("suppliers.service")


class SuppliersService:
    def __init__(self, repo: SuppliersRepository) -> None:
        self.repo = repo

    async def create_supplier(
        self,
        *,
        tenant_id: UUID,
        fields: dict[str, Any],
        created_by: UUID | None = None,
    ) -> Supplier:
        payload = {**fields, "tenant_id": tenant_id}
        if created_by is not None:
            payload["created_by"] = created_by
        return await self.repo.create_supplier(**payload)

    async def list_suppliers(
        self,
        *,
        tenant_id: UUID,
        include_inactive: bool = False,
    ) -> list[Supplier]:
        return await self.repo.list_suppliers(
            tenant_id=tenant_id,
            include_inactive=include_inactive,
        )

    async def search_suppliers(
        self,
        *,
        tenant_id: UUID,
        q: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Supplier], int, SupplierSearchSummaryData]:
        return await self.repo.search_suppliers(
            tenant_id=tenant_id,
            q=q,
            is_active=is_active,
            page=page,
            page_size=page_size,
        )

    async def search_supplier_options(
        self,
        *,
        tenant_id: UUID,
        q: str | None,
        include_inactive: bool,
        selected_id: UUID | None,
        limit: int,
    ) -> list[Supplier]:
        return await self.repo.search_supplier_options(
            tenant_id=tenant_id,
            q=q,
            include_inactive=include_inactive,
            selected_id=selected_id,
            limit=limit,
        )

    async def get_supplier(self, supplier_id: UUID, *, tenant_id: UUID) -> Supplier:
        supplier = await self.repo.get_supplier(supplier_id, tenant_id=tenant_id)
        if supplier is None:
            raise NotFoundError("Supplier not found")
        return supplier

    async def update_supplier(
        self,
        supplier_id: UUID,
        *,
        tenant_id: UUID,
        fields: dict[str, Any],
        updated_by: UUID | None = None,
    ) -> Supplier:
        supplier = await self.get_supplier(supplier_id, tenant_id=tenant_id)
        if updated_by is not None:
            fields = {**fields, "updated_by": updated_by}
        return await self.repo.update_supplier(supplier, **fields)

    async def create_return(
        self,
        *,
        operation_id: UUID,
        tenant_id: UUID,
        supplier_id: UUID,
        batch_id: UUID,
        qty: Decimal,
        reason: str,
        comment: str | None,
        source_document_id: UUID | None,
        actor_id: UUID | None,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> SupplierReturn:
        existing = await self.repo.get_return(operation_id, tenant_id=tenant_id)
        if existing is not None:
            self._assert_return_retry_matches(
                existing,
                supplier_id=supplier_id,
                batch_id=batch_id,
                qty=qty,
                reason=reason,
                comment=comment,
                source_document_id=source_document_id,
            )
            return existing

        await self.get_supplier(supplier_id, tenant_id=tenant_id)
        batch = await self.repo.get_batch_for_update(batch_id, tenant_id=tenant_id)
        if batch is None:
            raise NotFoundError("Batch not found")
        self._assert_branch_allowed(batch.branch_id, allowed_branch_ids=allowed_branch_ids)

        origin = await self.repo.get_batch_origin(batch_id, tenant_id=tenant_id)
        if origin is None:
            raise BusinessRuleError("Batch has no accepted incoming source")
        if origin.supplier_id != supplier_id:
            raise BusinessRuleError("Batch belongs to a different supplier")
        if origin.branch_id != batch.branch_id:
            raise BusinessRuleError("Batch source branch does not match the batch branch")
        if source_document_id is not None and source_document_id != origin.id:
            raise BusinessRuleError("Source document does not match the selected batch")

        amount = (batch.purchase_price * qty).quantize(Decimal("0.01"))
        try:
            async with self.repo.session.begin_nested():
                supplier_return = await self.repo.insert_return(
                    id=operation_id,
                    tenant_id=tenant_id,
                    supplier_id=supplier_id,
                    source_document_id=origin.id,
                    batch_id=batch_id,
                    qty=qty,
                    amount=amount,
                    currency=batch.currency,
                    reason=reason,
                    comment=comment,
                    created_by=actor_id,
                )
        except IntegrityError:
            existing = await self.repo.get_return(operation_id, tenant_id=tenant_id)
            if existing is None:
                raise
            self._assert_return_retry_matches(
                existing,
                supplier_id=supplier_id,
                batch_id=batch_id,
                qty=qty,
                reason=reason,
                comment=comment,
                source_document_id=origin.id,
            )
            return existing

        try:
            await InventoryRepository(self.repo.session).insert_movement(
                tenant_id=tenant_id,
                batch_id=batch_id,
                movement_type="supplier_return",
                qty_delta=-qty,
                source_table="supplier_return",
                source_id=supplier_return.id,
                operation_key=f"suppliers:return:{operation_id}",
                created_by=actor_id,
            )
        except (IntegrityError, InternalError) as exc:
            message = str(exc).lower()
            if "qty_remaining cannot be negative" in message or "qty_remaining" in message:
                raise BusinessRuleError(
                    "Return quantity exceeds batch remaining stock",
                    details={"requested": str(qty)},
                ) from exc
            raise

        await self.repo.session.refresh(batch)
        logger.info(
            "supplier_return",
            batch_id=str(batch_id),
            supplier_id=str(supplier_id),
            qty=str(qty),
        )
        return supplier_return

    async def search_return_candidates(
        self,
        *,
        tenant_id: UUID,
        supplier_id: UUID,
        branch_id: UUID | None,
        branch_ids: set[UUID] | None,
        q: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[SupplierReturnCandidateRow], int]:
        await self.get_supplier(supplier_id, tenant_id=tenant_id)
        if branch_id is not None:
            self._assert_branch_allowed(branch_id, allowed_branch_ids=branch_ids)
        return await self.repo.search_return_candidates(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            branch_id=branch_id,
            branch_ids=branch_ids,
            q=q,
            page=page,
            page_size=page_size,
        )

    async def search_returns(
        self,
        *,
        tenant_id: UUID,
        supplier_id: UUID | None,
        branch_id: UUID | None,
        branch_ids: set[UUID] | None,
        reason: str | None,
        date_from: date | None,
        date_to: date | None,
        page: int,
        page_size: int,
    ) -> tuple[list[SupplierReturnRow], SupplierReturnSummaryData, str]:
        if supplier_id is not None:
            await self.get_supplier(supplier_id, tenant_id=tenant_id)
        if branch_id is not None:
            self._assert_branch_allowed(branch_id, allowed_branch_ids=branch_ids)
        if date_from is not None and date_to is not None and date_from > date_to:
            raise BusinessRuleError("date_from cannot be later than date_to")

        settings = await FoundationRepository(self.repo.session).get_settings(tenant_id)
        timezone_name = settings.report_timezone if settings is not None else "Asia/Dushanbe"
        try:
            created_from = local_day_range(date_from, timezone_name)[0] if date_from else None
            created_to = local_day_range(date_to, timezone_name)[1] if date_to else None
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise AurumError("Tenant report timezone is invalid") from exc

        rows, summary = await self.repo.search_returns(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            branch_id=branch_id,
            branch_ids=branch_ids,
            reason=reason,
            created_from=created_from,
            created_to=created_to,
            page=page,
            page_size=page_size,
        )
        return rows, summary, timezone_name

    @staticmethod
    def _assert_branch_allowed(
        branch_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None,
    ) -> None:
        if allowed_branch_ids is not None and branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Branch access denied")

    @staticmethod
    def _assert_return_retry_matches(
        existing: SupplierReturn,
        *,
        supplier_id: UUID,
        batch_id: UUID,
        qty: Decimal,
        reason: str,
        comment: str | None,
        source_document_id: UUID | None,
    ) -> None:
        if (
            existing.supplier_id != supplier_id
            or existing.batch_id != batch_id
            or existing.qty != qty
            or existing.reason != reason
            or existing.comment != comment
            or (
                source_document_id is not None and existing.source_document_id != source_document_id
            )
        ):
            raise BusinessRuleError("Supplier return operation key was reused with different data")
