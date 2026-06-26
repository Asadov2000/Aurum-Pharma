"""Business logic for the suppliers domain.

Note on supplier_return — the spec asks for a warning if the batch did
not come from this supplier originally. We look up the latest incoming
document that produced this batch through incoming_item.created_batch_id;
if it doesn't match (or doesn't exist), we set a warning string without
blocking the return.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InternalError

from app.core.errors import BusinessRuleError, NotFoundError
from app.domains.incoming.models import IncomingDocument
from app.domains.inventory.models import Batch
from app.domains.inventory.repository import InventoryRepository
from app.domains.suppliers.models import Supplier, SupplierReturn
from app.domains.suppliers.repository import SuppliersRepository

logger = structlog.get_logger("suppliers.service")


class SuppliersService:
    def __init__(self, repo: SuppliersRepository) -> None:
        self.repo = repo

    # ---- supplier CRUD ----

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

    async def list_suppliers(self, *, include_inactive: bool = False) -> list[Supplier]:
        return await self.repo.list_suppliers(include_inactive=include_inactive)

    async def get_supplier(self, supplier_id: UUID) -> Supplier:
        supplier = await self.repo.get_supplier(supplier_id)
        if supplier is None:
            raise NotFoundError("Supplier not found")
        return supplier

    async def update_supplier(
        self,
        supplier_id: UUID,
        *,
        fields: dict[str, Any],
        updated_by: UUID | None = None,
    ) -> Supplier:
        supplier = await self.get_supplier(supplier_id)
        if updated_by is not None:
            fields = {**fields, "updated_by": updated_by}
        return await self.repo.update_supplier(supplier, **fields)

    # ---- supplier_return ----

    async def create_return(
        self,
        *,
        tenant_id: UUID,
        supplier_id: UUID,
        batch_id: UUID,
        qty: Decimal,
        reason: str,
        comment: str | None,
        source_document_id: UUID | None,
        actor_id: UUID | None,
    ) -> tuple[SupplierReturn, str | None]:
        """Returns (supplier_return, warning_or_none)."""
        # Validate supplier and batch exist
        supplier = await self.get_supplier(supplier_id)
        if supplier.tenant_id != tenant_id:
            raise NotFoundError("Supplier not found")
        if source_document_id is not None:
            await self._assert_source_document_in_tenant(
                source_document_id,
                tenant_id=tenant_id,
            )

        inv_repo = InventoryRepository(self.repo.session)
        batch = await inv_repo.get_batch(batch_id)
        if batch is None or batch.tenant_id != tenant_id:
            raise NotFoundError("Batch not found")

        # Soft check: was this batch really supplied by this supplier?
        warning = await self._cross_supplier_warning(batch_id=batch_id, supplier_id=supplier_id)

        amount = (batch.purchase_price * qty).quantize(Decimal("0.01"))

        sr = await self.repo.insert_return(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            source_document_id=source_document_id,
            batch_id=batch_id,
            qty=qty,
            amount=amount,
            currency=batch.currency,
            reason=reason,
            comment=comment,
            created_by=actor_id,
        )

        # Inventory movement (the trigger guards against negative qty)
        try:
            await inv_repo.insert_movement(
                tenant_id=tenant_id,
                batch_id=batch_id,
                movement_type="supplier_return",
                qty_delta=-qty,
                source_table="supplier_return",
                source_id=sr.id,
                created_by=actor_id,
            )
        except (IntegrityError, InternalError) as exc:
            msg = str(exc).lower()
            if "qty_remaining cannot be negative" in msg or "qty_remaining" in msg:
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
            warning=warning,
        )
        _ = supplier  # silence unused (held for permissions hook later)
        return sr, warning

    async def list_returns(
        self,
        *,
        supplier_id: UUID | None = None,
        date_from: date | datetime | None = None,
        date_to: date | datetime | None = None,
    ) -> list[SupplierReturn]:
        return await self.repo.list_returns(
            supplier_id=supplier_id, date_from=date_from, date_to=date_to
        )

    # ---- helpers ----

    async def _cross_supplier_warning(self, *, batch_id: UUID, supplier_id: UUID) -> str | None:
        """If we can find an incoming_item that created this batch and its
        document.supplier_id != supplier_id — return a warning string."""
        from app.domains.incoming.models import IncomingDocument, IncomingItem

        stmt = (
            select(IncomingDocument.supplier_id)
            .join(IncomingItem, IncomingItem.document_id == IncomingDocument.id)
            .where(IncomingItem.created_batch_id == batch_id)
            .limit(1)
        )
        result = await self.repo.session.execute(stmt)
        original_supplier = result.scalar_one_or_none()
        if original_supplier is None:
            return None  # batch wasn't created via accept — can't check
        if original_supplier != supplier_id:
            return (
                "Batch was originally supplied by a different supplier; "
                "the return is recorded anyway."
            )
        return None

    async def _assert_source_document_in_tenant(
        self,
        source_document_id: UUID,
        *,
        tenant_id: UUID,
    ) -> None:
        doc = await self.repo.session.get(IncomingDocument, source_document_id)
        if doc is None or doc.tenant_id != tenant_id:
            raise NotFoundError("Incoming document not found")


# Keep imports referenced for the type-checkers / linters.
_ = (Batch,)
