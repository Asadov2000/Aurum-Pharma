"""Compare current scoped legacy stock with the caller's explicit physical count."""

from uuid import UUID

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.unit_legacy_reconciliation import (
    LegacyReconciliationObservation,
    LegacyReconciliationPreview,
    preview_legacy_reconciliation,
)
from app.domains.inventory.unit_legacy_reconciliation_repository import (
    LegacyReconciliationPreviewRepository,
)
from app.domains.inventory.unit_legacy_reconciliation_schemas import (
    LegacyReconciliationPreviewRequest,
)


class LegacyReconciliationPreviewService:
    def __init__(self, repository: LegacyReconciliationPreviewRepository) -> None:
        self.repository = repository

    async def preview(
        self,
        *,
        tenant_id: UUID,
        batch_id: UUID,
        request: LegacyReconciliationPreviewRequest,
        allowed_branch_ids: set[UUID] | None,
    ) -> LegacyReconciliationPreview:
        request = LegacyReconciliationPreviewRequest.model_validate(request.model_dump())
        if allowed_branch_ids is not None and request.branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Batch viewing permission denied")
        batch = await self.repository.read_batch(
            tenant_id=tenant_id, branch_id=request.branch_id, batch_id=batch_id
        )
        if batch is None:
            raise NotFoundError("Legacy batch not found")
        if (batch.source.tenant_id, batch.source.branch_id, batch.source.batch_id) != (
            tenant_id,
            request.branch_id,
            batch_id,
        ):
            raise NotFoundError("Legacy batch not found")
        if batch.has_preparation or batch.has_physical_snapshot:
            raise ConflictError("Unit-prepared stock cannot be compared as legacy stock")
        observation = LegacyReconciliationObservation(
            **request.model_dump(exclude={"branch_id"}),
            tenant_id=tenant_id,
            branch_id=request.branch_id,
            catalog_id=batch.source.catalog_id,
            batch_id=batch_id,
        )
        return preview_legacy_reconciliation(
            batch.source, observation, tenant_id=tenant_id, branch_id=request.branch_id
        )
