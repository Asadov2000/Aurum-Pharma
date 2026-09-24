"""Import supplier rows into the existing, reviewable incoming draft queue."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.incoming.draft_service import MAX_PENDING_DRAFT_ROWS
from app.domains.incoming.invoice_parser import (
    InvoiceTable,
    parse_invoice,
    suggested_mapping,
    validate_mapping,
)
from app.domains.incoming.models import IncomingDraftRow
from app.domains.incoming.schemas import IncomingDraftFields
from app.domains.incoming.service import IncomingService


def _row_id(document_id: UUID, file_digest: str, index: int) -> UUID:
    """Stable UUID4-shaped row ID also accepted by existing draft routes."""
    digest = hashlib.sha256(f"{document_id}:{file_digest}:{index}".encode("ascii")).digest()
    return UUID(bytes=digest[:16], version=4)


def _mapped_rows(table: InvoiceTable, mapping: dict[str, int | None]) -> list[IncomingDraftFields]:
    rows: list[IncomingDraftFields] = []
    invalid: list[int] = []
    for number, cells in enumerate(table.rows, start=2):
        values = {
            field: cells[index] if index is not None and index < len(cells) else ""
            for field, index in mapping.items()
        }
        if not values["source_name"].strip():
            invalid.append(number)
            continue
        try:
            rows.append(IncomingDraftFields(catalog_id="", **values))
        except PydanticValidationError:
            invalid.append(number)
    if invalid:
        raise BusinessRuleError(
            "Invoice rows need review",
            details={"reason": "invoice_row_invalid", "rows": invalid[:25], "total": len(invalid)},
        )
    return rows


class IncomingInvoiceService:
    def __init__(self, incoming: IncomingService) -> None:
        self.incoming = incoming
        self.repo = incoming.repo

    async def preview(
        self,
        document_id: UUID,
        raw: bytes,
        filename: str | None,
        *,
        allowed_branch_ids: set[UUID] | None,
    ) -> dict[str, object]:
        doc = await self.incoming.get_document(document_id, allowed_branch_ids=allowed_branch_ids)
        self.incoming._assert_draft(doc)
        table = parse_invoice(raw, filename)
        mapping = suggested_mapping(table.headers)
        warnings = (
            ["Не удалось определить колонку названия товара"]
            if mapping["source_name"] is None
            else []
        )
        return {
            "headers": table.headers,
            "rows": table.rows[:20],
            "suggested_mapping": mapping,
            "total_rows": len(table.rows),
            "warnings": warnings,
        }

    async def import_rows(
        self,
        document_id: UUID,
        raw: bytes,
        filename: str | None,
        *,
        mapping: object,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> dict[str, int]:
        # Parse and validate every row before opening a write transaction. No
        # partial draft is left behind when one supplier cell is malformed.
        table = parse_invoice(raw, filename)
        checked_mapping = validate_mapping(mapping, len(table.headers))
        fields = _mapped_rows(table, checked_mapping)
        doc = await self.incoming._get_document_for_mutation(
            document_id, allowed_branch_ids=allowed_branch_ids
        )
        self.incoming._assert_draft(doc)
        await self.repo.lock_operation_id(tenant_id=doc.tenant_id, operation_id=operation_id)

        # File identity deliberately excludes operation_id: a fresh click with
        # a new key on the same file cannot create duplicate rows. A retry after
        # some rows were committed or edited still finds their durable IDs.
        file_digest = hashlib.sha256(raw).hexdigest()
        mapping_digest = hashlib.sha256(
            json.dumps(checked_mapping, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        previous_operation = await self.repo.get_draft_rows_by_invoice_operation(
            doc.tenant_id, operation_id
        )
        if any(
            row.document_id != document_id
            or row.fields.get("_invoice_file_hash") != file_digest
            or row.fields.get("_invoice_mapping_hash") != mapping_digest
            for row in previous_operation
        ):
            raise ConflictError(
                "Invoice import operation changed",
                details={"reason": "invoice_import_conflict"},
            )
        identifiers = [_row_id(document_id, file_digest, index) for index in range(len(fields))]
        existing = await self.repo.get_draft_rows_by_ids(doc.tenant_id, identifiers)
        if any(
            row.document_id != document_id
            or row.fields.get("_invoice_file_hash") != file_digest
            or row.fields.get("_invoice_mapping_hash") != mapping_digest
            for row in existing.values()
        ):
            raise ConflictError(
                "Invoice row identity conflict", details={"reason": "invoice_import_conflict"}
            )
        new_count = len(fields) - len(existing)
        if await self.repo.count_draft_rows(document_id) + new_count > MAX_PENDING_DRAFT_ROWS:
            raise BusinessRuleError(
                "Too many pending draft rows",
                details={"reason": "invoice_row_limit", "limit": MAX_PENDING_DRAFT_ROWS},
            )
        for index, field in enumerate(fields):
            if identifiers[index] in existing:
                continue
            self.repo.session.add(
                IncomingDraftRow(
                    tenant_id=doc.tenant_id,
                    id=identifiers[index],
                    document_id=document_id,
                    revision=1,
                    status="pending",
                    fields={
                        **field.as_fields(),
                        "_invoice_file_hash": file_digest,
                        "_invoice_mapping_hash": mapping_digest,
                        "_invoice_operation_id": str(operation_id),
                    },
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
        await self.repo.session.flush()
        return {"imported_count": new_count, "existing_count": len(existing)}
