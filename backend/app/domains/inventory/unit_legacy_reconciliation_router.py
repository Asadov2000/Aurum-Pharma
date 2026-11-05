"""Legacy count preview and immutable observations, without stock conversion."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import PermissionDeniedError
from app.domains.inventory.unit_legacy_reconciliation import LegacyReconciliationPreview
from app.domains.inventory.unit_legacy_reconciliation_record_repository import (
    LegacyReconciliationRecordRepository,
)
from app.domains.inventory.unit_legacy_reconciliation_record_service import (
    LegacyReconciliationRecordService,
)
from app.domains.inventory.unit_legacy_reconciliation_repository import (
    LegacyReconciliationPreviewRepository,
)
from app.domains.inventory.unit_legacy_reconciliation_schemas import (
    LegacyReconciliationComparisonResponse,
    LegacyReconciliationPreviewRequest,
    LegacyReconciliationRecordResponse,
    LegacyReconciliationSaveRequest,
)
from app.domains.inventory.unit_legacy_reconciliation_service import (
    LegacyReconciliationPreviewService,
)

router = APIRouter()


@router.get(
    "/{batch_id}/unit-reconciliation/records/{record_id}/comparison",
    response_model=LegacyReconciliationComparisonResponse,
)
async def compare_legacy_batch_reconciliation(
    batch_id: UUID,
    record_id: UUID,
    branch_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.view", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> LegacyReconciliationComparisonResponse:
    if user.tenant_id is None or not user.can_access_branch("batches.view", branch_id):
        raise PermissionDeniedError("Batch viewing permission denied")
    response.headers["Cache-Control"] = "private, no-store"
    return await LegacyReconciliationRecordService(
        LegacyReconciliationRecordRepository(db)
    ).compare(
        tenant_id=user.tenant_id,
        branch_id=branch_id,
        batch_id=batch_id,
        record_id=record_id,
        view_branch_ids=user.branch_scope_for("batches.view"),
    )


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


@router.post(
    "/{batch_id}/unit-reconciliation/records",
    response_model=LegacyReconciliationRecordResponse,
    dependencies=[Depends(require_writable_tenant)],
)
async def save_legacy_batch_reconciliation(
    batch_id: UUID,
    payload: LegacyReconciliationSaveRequest,
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.update", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> LegacyReconciliationRecordResponse:
    if (
        user.tenant_id is None
        or not user.can_access_branch("batches.view", payload.branch_id)
        or not user.can_access_branch("batches.update", payload.branch_id)
    ):
        raise PermissionDeniedError("Batch reconciliation permission denied")
    response.headers["Cache-Control"] = "private, no-store"
    return await LegacyReconciliationRecordService(LegacyReconciliationRecordRepository(db)).save(
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        batch_id=batch_id,
        request=payload,
        view_branch_ids=user.branch_scope_for("batches.view"),
        update_branch_ids=user.branch_scope_for("batches.update"),
    )


@router.get(
    "/{batch_id}/unit-reconciliation/records/{record_id}",
    response_model=LegacyReconciliationRecordResponse,
)
async def read_legacy_batch_reconciliation(
    batch_id: UUID,
    record_id: UUID,
    branch_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.view", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> LegacyReconciliationRecordResponse:
    if user.tenant_id is None or not user.can_access_branch("batches.view", branch_id):
        raise PermissionDeniedError("Batch viewing permission denied")
    response.headers["Cache-Control"] = "private, no-store"
    return await LegacyReconciliationRecordService(LegacyReconciliationRecordRepository(db)).read(
        tenant_id=user.tenant_id,
        branch_id=branch_id,
        batch_id=batch_id,
        record_id=record_id,
        view_branch_ids=user.branch_scope_for("batches.view"),
    )
