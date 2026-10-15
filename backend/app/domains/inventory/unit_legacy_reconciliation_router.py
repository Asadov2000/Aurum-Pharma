"""Read-only preview against database-owned legacy quantities, without conversion."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission
from app.core.errors import PermissionDeniedError
from app.domains.inventory.unit_legacy_reconciliation import LegacyReconciliationPreview
from app.domains.inventory.unit_legacy_reconciliation_repository import (
    LegacyReconciliationPreviewRepository,
)
from app.domains.inventory.unit_legacy_reconciliation_schemas import (
    LegacyReconciliationPreviewRequest,
)
from app.domains.inventory.unit_legacy_reconciliation_service import (
    LegacyReconciliationPreviewService,
)

router = APIRouter()


@router.post("/{batch_id}/unit-reconciliation/preview", response_model=LegacyReconciliationPreview)
async def preview_legacy_batch_reconciliation(
    batch_id: UUID,
    payload: LegacyReconciliationPreviewRequest,
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.view", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> LegacyReconciliationPreview:
    if user.tenant_id is None or not user.can_access_branch("batches.view", payload.branch_id):
        raise PermissionDeniedError("Batch viewing permission denied")
    response.headers["Cache-Control"] = "private, no-store"
    return await LegacyReconciliationPreviewService(
        LegacyReconciliationPreviewRepository(db)
    ).preview(
        tenant_id=user.tenant_id,
        batch_id=batch_id,
        request=payload,
        allowed_branch_ids=user.branch_scope_for("batches.view"),
    )
