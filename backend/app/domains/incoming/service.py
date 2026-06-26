"""Business logic for the incoming domain.

Lifecycle: a document starts as `draft`; you can add/modify/delete its
items as long as it stays in draft. `accept` is a one-way transition
that creates a `batch` per item and a corresponding `batch_movement`
of type 'incoming', then locks the document. `reject` also locks but
without creating stock.

`total_amount` is recomputed in this service after every item change
so the read-side and the document body stay in sync without a DB
trigger.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.time import utc_now
from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Branch
from app.domains.incoming.models import IncomingDocument, IncomingItem
from app.domains.incoming.repository import IncomingRepository
from app.domains.inventory.repository import InventoryRepository
from app.domains.suppliers.models import Supplier

logger = structlog.get_logger("incoming.service")


class IncomingService:
    def __init__(self, repo: IncomingRepository) -> None:
        self.repo = repo

    # ---- documents ----

    async def create_document(
        self,
        *,
        tenant_id: UUID,
        fields: dict[str, Any],
        created_by: UUID | None = None,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> IncomingDocument:
        await self._assert_document_refs_in_tenant(
            tenant_id=tenant_id,
            branch_id=fields.get("branch_id"),
            supplier_id=fields.get("supplier_id"),
        )
        branch_id = fields.get("branch_id")
        if isinstance(branch_id, UUID):
            self._assert_branch_allowed(branch_id, allowed_branch_ids=allowed_branch_ids)
        payload = {**fields, "tenant_id": tenant_id, "status": "draft"}
        if created_by is not None:
            payload["created_by"] = created_by
        return await self.repo.create_document(**payload)

    async def list_documents(
        self,
        *,
        branch_id: UUID | None = None,
        supplier_id: UUID | None = None,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[IncomingDocument]:
        return await self.repo.list_documents(
            branch_id=branch_id,
            supplier_id=supplier_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )

    async def get_document(
        self,
        document_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> IncomingDocument:
        doc = await self.repo.get_document(document_id)
        if doc is None:
            raise NotFoundError("Incoming document not found")
        self._assert_branch_allowed(doc.branch_id, allowed_branch_ids=allowed_branch_ids)
        return doc

    async def list_items(
        self,
        document_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> list[IncomingItem]:
        await self.get_document(
            document_id,
            allowed_branch_ids=allowed_branch_ids,
        )
        return await self.repo.list_items(document_id)

    async def update_document(
        self,
        document_id: UUID,
        *,
        fields: dict[str, Any],
        updated_by: UUID | None = None,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> IncomingDocument:
        doc = await self.get_document(document_id, allowed_branch_ids=allowed_branch_ids)
        self._assert_draft(doc)
        await self._assert_document_refs_in_tenant(
            tenant_id=doc.tenant_id,
            branch_id=fields.get("branch_id"),
            supplier_id=fields.get("supplier_id"),
        )
        branch_id = fields.get("branch_id")
        if isinstance(branch_id, UUID):
            self._assert_branch_allowed(branch_id, allowed_branch_ids=allowed_branch_ids)
        if updated_by is not None:
            fields = {**fields, "updated_by": updated_by}
        return await self.repo.update_document(doc, **fields)

    # ---- items ----

    async def add_item(
        self,
        document_id: UUID,
        *,
        fields: dict[str, Any],
        allowed_branch_ids: set[UUID] | None = None,
    ) -> IncomingItem:
        doc = await self.get_document(document_id, allowed_branch_ids=allowed_branch_ids)
        self._assert_draft(doc)
        await self._assert_catalog_in_tenant(fields["catalog_id"], tenant_id=doc.tenant_id)
        payload = {**fields, "document_id": doc.id, "tenant_id": doc.tenant_id}
        item = await self.repo.create_item(**payload)
        await self._recompute_total(doc)
        return item

    async def update_item(
        self,
        document_id: UUID,
        item_id: UUID,
        *,
        fields: dict[str, Any],
        allowed_branch_ids: set[UUID] | None = None,
    ) -> IncomingItem:
        doc = await self.get_document(document_id, allowed_branch_ids=allowed_branch_ids)
        self._assert_draft(doc)
        if "catalog_id" in fields:
            await self._assert_catalog_in_tenant(fields["catalog_id"], tenant_id=doc.tenant_id)
        item = await self.repo.get_item(item_id)
        if item is None or item.document_id != document_id:
            raise NotFoundError("Item not found")
        updated = await self.repo.update_item(item, **fields)
        await self._recompute_total(doc)
        return updated

    async def delete_item(
        self,
        document_id: UUID,
        item_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> None:
        doc = await self.get_document(document_id, allowed_branch_ids=allowed_branch_ids)
        self._assert_draft(doc)
        rows = await self.repo.delete_item(item_id, document_id=document_id)
        if rows == 0:
            raise NotFoundError("Item not found")
        await self._recompute_total(doc)

    # ---- accept / reject ----

    async def accept(
        self,
        document_id: UUID,
        *,
        actor_id: UUID | None = None,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> IncomingDocument:
        doc = await self.get_document(document_id, allowed_branch_ids=allowed_branch_ids)
        self._assert_draft(doc)
        items = await self.repo.list_items(document_id)
        if not items:
            raise BusinessRuleError("Cannot accept a document with no items")
        today = utc_now().date()
        for item in items:
            if item.qty <= 0:
                raise BusinessRuleError(
                    "Item qty must be positive",
                    details={"item_id": str(item.id)},
                )
            if item.expires_at <= today:
                raise BusinessRuleError(
                    "Item expires_at must be in the future",
                    details={"item_id": str(item.id), "expires_at": item.expires_at.isoformat()},
                )

        inv_repo = InventoryRepository(self.repo.session)
        for item in items:
            batch = await inv_repo.create_batch(
                tenant_id=doc.tenant_id,
                branch_id=doc.branch_id,
                catalog_id=item.catalog_id,
                batch_number=item.batch_number,
                manufactured_at=item.manufactured_at,
                expires_at=item.expires_at,
                purchase_price=item.purchase_price,
                sale_price=item.sale_price,
                qty_initial=item.qty,
                qty_remaining=Decimal("0"),  # raised by the trigger via insert_movement
                created_by=actor_id,
            )
            await inv_repo.insert_movement(
                tenant_id=doc.tenant_id,
                batch_id=batch.id,
                movement_type="incoming",
                qty_delta=item.qty,
                source_table="incoming_item",
                source_id=item.id,
                created_by=actor_id,
            )
            await self.repo.update_item(item, created_batch_id=batch.id)
            await self.repo.session.refresh(batch)

        await self.repo.update_document(
            doc,
            status="accepted",
            accepted_at=utc_now(),
            accepted_by=actor_id,
        )
        logger.info("incoming_accepted", document_id=str(doc.id), items=len(items))
        return doc

    async def reject(
        self,
        document_id: UUID,
        *,
        actor_id: UUID | None = None,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> IncomingDocument:
        doc = await self.get_document(document_id, allowed_branch_ids=allowed_branch_ids)
        self._assert_draft(doc)
        await self.repo.update_document(doc, status="rejected", updated_by=actor_id)
        logger.info("incoming_rejected", document_id=str(doc.id))
        return doc

    # ---- helpers ----

    @staticmethod
    def _assert_draft(doc: IncomingDocument) -> None:
        if doc.status != "draft":
            raise BusinessRuleError(
                "Cannot modify a document that is already accepted or rejected",
                details={"status": doc.status},
            )

    @staticmethod
    def _assert_branch_allowed(
        branch_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None,
    ) -> None:
        if allowed_branch_ids is not None and branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Branch access denied")

    async def _assert_document_refs_in_tenant(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID | None,
        supplier_id: UUID | None,
    ) -> None:
        if branch_id is not None:
            branch = await self.repo.session.get(Branch, branch_id)
            if branch is None or branch.tenant_id != tenant_id:
                raise NotFoundError("Branch not found")
        if supplier_id is not None:
            supplier = await self.repo.session.get(Supplier, supplier_id)
            if supplier is None or supplier.tenant_id != tenant_id:
                raise NotFoundError("Supplier not found")

    async def _assert_catalog_in_tenant(self, catalog_id: UUID, *, tenant_id: UUID) -> None:
        item = await self.repo.session.get(TenantCatalog, catalog_id)
        if item is None or item.tenant_id != tenant_id:
            raise NotFoundError("Catalog item not found")

    async def _recompute_total(self, doc: IncomingDocument) -> None:
        total = await self.repo.total_amount(doc.id)
        await self.repo.update_document(doc, total_amount=Decimal(str(total)))
