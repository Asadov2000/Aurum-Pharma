"""Persist bounded unfinished input separately from valid receipt/inventory rows."""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.incoming.models import IncomingDraftRow, IncomingItem
from app.domains.incoming.repository import IncomingDocumentDetails
from app.domains.incoming.schemas import IncomingDraftFields, IncomingItemBase
from app.domains.incoming.service import IncomingService

MAX_PENDING_DRAFT_ROWS = 500


class IncomingDraftService:
    def __init__(self, incoming: IncomingService) -> None:
        self.incoming = incoming
        self.repo = incoming.repo

    async def list_rows(
        self,
        document_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None,
    ) -> tuple[IncomingDocumentDetails, list[IncomingDraftRow], dict[str, str]]:
        details = await self.incoming.get_document_details(
            document_id, allowed_branch_ids=allowed_branch_ids
        )
        rows = await self.repo.list_draft_rows(document_id)
        return details, rows, await self.catalog_names(details.document.tenant_id, rows)

    async def catalog_names(self, tenant_id: UUID, rows: list[IncomingDraftRow]) -> dict[str, str]:
        return await self.repo.draft_catalog_names(
            tenant_id, {row.fields["catalog_id"] for row in rows if row.fields["catalog_id"]}
        )

    async def save(
        self,
        document_id: UUID,
        row_id: UUID,
        *,
        expected_revision: int,
        fields: IncomingDraftFields,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> IncomingDraftRow:
        doc = await self.incoming._get_document_for_mutation(
            document_id, allowed_branch_ids=allowed_branch_ids
        )
        self.incoming._assert_draft(doc)
        await self.repo.lock_operation_id(tenant_id=doc.tenant_id, operation_id=row_id)
        row = await self.repo.get_draft_row(doc.tenant_id, row_id)
        raw_fields = fields.as_fields()
        if row is None:
            if expected_revision != 0:
                self._conflict(0)
            if await self.repo.count_draft_rows(document_id) >= MAX_PENDING_DRAFT_ROWS:
                raise BusinessRuleError(
                    "Too many pending draft rows",
                    details={"reason": "draft_row_limit", "limit": MAX_PENDING_DRAFT_ROWS},
                )
            row = IncomingDraftRow(
                tenant_id=doc.tenant_id,
                id=row_id,
                document_id=document_id,
                revision=1,
                status="pending",
                fields=raw_fields,
                created_by=actor_id,
                updated_by=actor_id,
            )
        else:
            self._assert_document(row, document_id)
            if row.status != "pending":
                self._conflict(row.revision, closed=True)
            if row.revision == expected_revision + 1 and row.fields == raw_fields:
                return row
            if row.revision != expected_revision:
                self._conflict(row.revision)
            row.fields = raw_fields
            row.revision += 1
            row.updated_by = actor_id
        return await self.repo.persist_draft_row(row)

    async def discard(
        self,
        document_id: UUID,
        row_id: UUID,
        *,
        expected_revision: int,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> None:
        doc = await self.incoming._get_document_for_mutation(
            document_id, allowed_branch_ids=allowed_branch_ids
        )
        self.incoming._assert_draft(doc)
        row = await self._require_row(doc.tenant_id, document_id, row_id)
        if row.status == "discarded" and row.revision == expected_revision + 1:
            return
        if row.status != "pending":
            self._conflict(row.revision, closed=True)
        if row.revision != expected_revision:
            self._conflict(row.revision)
        row.status = "discarded"
        row.revision += 1
        row.updated_by = actor_id
        await self.repo.persist_draft_row(row)

    async def commit(
        self,
        document_id: UUID,
        row_id: UUID,
        *,
        expected_revision: int,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> IncomingItem:
        doc = await self.incoming._get_document_for_mutation(
            document_id, allowed_branch_ids=allowed_branch_ids
        )
        row = await self._require_row(doc.tenant_id, document_id, row_id)
        if row.status == "committed" and row.revision == expected_revision + 1:
            item = await self.repo.get_committed_draft_item(row)
            if item is None:
                raise NotFoundError("Committed incoming item not found")
            return item
        self.incoming._assert_draft(doc)
        if row.status != "pending":
            self._conflict(row.revision, closed=True)
        if row.revision != expected_revision:
            self._conflict(row.revision)
        payload = self._validated_item(row)
        # A savepoint makes conversion all-or-nothing even for internal callers
        # that catch an exception and continue using their outer transaction.
        async with self.repo.session.begin_nested():
            item = await self.incoming.add_item(
                document_id,
                operation_id=row.id,
                fields=payload.model_dump(exclude={"operation_id"}),
                allowed_branch_ids=allowed_branch_ids,
            )
            row.status = "committed"
            row.revision += 1
            row.committed_item_id = item.id
            row.updated_by = actor_id
            await self.repo.persist_draft_row(row)
        return item

    async def _require_row(
        self, tenant_id: UUID, document_id: UUID, row_id: UUID
    ) -> IncomingDraftRow:
        row = await self.repo.get_draft_row(tenant_id, row_id)
        if row is None:
            raise NotFoundError("Draft row not found")
        self._assert_document(row, document_id)
        return row

    @staticmethod
    def _assert_document(row: IncomingDraftRow, document_id: UUID) -> None:
        if row.document_id != document_id:
            raise NotFoundError("Draft row not found")

    @staticmethod
    def _conflict(revision: int, *, closed: bool = False) -> None:
        raise ConflictError(
            "Draft row changed; reload before continuing",
            details={
                "reason": "draft_row_closed" if closed else "draft_revision_conflict",
                "current_revision": revision,
            },
        )

    @staticmethod
    def _validated_item(row: IncomingDraftRow) -> IncomingItemBase:
        try:
            raw = IncomingDraftFields.model_validate(row.fields)
        except PydanticValidationError:
            raise BusinessRuleError(
                "Invalid draft fields",
                details={"reason": "draft_fields_invalid"},
            ) from None
        invalid: set[str] = set()
        for field in ("qty", "purchase_price", "sale_price"):
            if not re.fullmatch(r"\d+(?:[.,]\d+)?", row.fields[field].strip(), flags=re.ASCII):
                invalid.add(field)
        for field in ("manufactured_at", "expires_at"):
            value = row.fields[field].strip()
            if (value or field == "expires_at") and not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", value, flags=re.ASCII
            ):
                invalid.add(field)
        try:
            payload = IncomingItemBase.model_validate(
                {
                    "operation_id": row.id,
                    "catalog_id": raw.catalog_id,
                    "batch_number": raw.batch_number.strip() or None,
                    "manufactured_at": raw.manufactured_at.strip() or None,
                    "expires_at": raw.expires_at.strip(),
                    "qty": raw.qty.strip().replace(",", "."),
                    "purchase_price": raw.purchase_price.strip().replace(",", "."),
                    "sale_price": raw.sale_price.strip().replace(",", "."),
                }
            )
        except PydanticValidationError as exc:
            invalid.update(str(error["loc"][0]) for error in exc.errors(include_input=False))
            raise BusinessRuleError(
                "Complete the draft row before adding it",
                details={"reason": "draft_fields_invalid", "fields": sorted(invalid)},
            ) from None
        if invalid:
            raise BusinessRuleError(
                "Complete the draft row before adding it",
                details={"reason": "draft_fields_invalid", "fields": sorted(invalid)},
            )
        return payload
