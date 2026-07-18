"""FastAPI endpoints for the inventory domain."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_permission
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.schemas import (
    BatchDetails,
    BatchList,
    BatchRead,
    BatchWithExpiry,
    MovementRead,
    WriteOffCreate,
    WriteOffRead,
)
from app.domains.inventory.service import InventoryService

router = APIRouter(prefix="/api/v1/batches", tags=["inventory"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> InventoryService:
    return InventoryService(InventoryRepository(db))


@router.get("", response_model=BatchList)
async def list_batches(
    # reports.view (owner/admin/dev): batch rows carry purchase_price (margins),
    # which must not be visible to sellers. POS doesn't read /batches — FEFO
    # picks batches server-side — so the cashier flow is unaffected.
    user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
    service: Annotated[InventoryService, Depends(_service)],
    catalog_id: Annotated[UUID | None, Query()] = None,
    branch_id: Annotated[UUID | None, Query()] = None,
    expiry_status: Annotated[str | None, Query()] = None,
    show_empty: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> BatchList:
    branch_scope = user.branch_scope_for("reports.view")
    if branch_scope is not None and branch_id is not None and branch_id not in branch_scope:
        return BatchList(items=[], total=0, page=page, page_size=page_size)
    rows, total = await service.list_batches(
        catalog_id=catalog_id,
        branch_id=branch_id,
        expiry_status=expiry_status,
        show_empty=show_empty,
        page=page,
        page_size=page_size,
        branch_ids=branch_scope,
    )
    return BatchList(
        items=[BatchWithExpiry(**row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{batch_id}", response_model=BatchDetails)
async def get_batch(
    batch_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
    service: Annotated[InventoryService, Depends(_service)],
) -> BatchDetails:
    branch_scope = user.branch_scope_for("reports.view")
    batch = await service.get_batch(batch_id, allowed_branch_ids=branch_scope)
    recent = await service.list_movements(
        batch_id,
        limit=20,
        allowed_branch_ids=branch_scope,
    )
    return BatchDetails(
        **BatchRead.model_validate(batch).model_dump(),
        recent_movements=[MovementRead.model_validate(m) for m in recent],
    )


@router.get("/{batch_id}/movements", response_model=list[MovementRead])
async def list_movements(
    batch_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
    service: Annotated[InventoryService, Depends(_service)],
) -> list[MovementRead]:
    movements = await service.list_movements(
        batch_id,
        allowed_branch_ids=user.branch_scope_for("reports.view"),
    )
    return [MovementRead.model_validate(m) for m in movements]


@router.post(
    "/{batch_id}/write-off",
    response_model=WriteOffRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_write_off(
    batch_id: UUID,
    payload: WriteOffCreate,
    user: Annotated[CurrentUser, Depends(require_permission("batches.write_off"))],
    service: Annotated[InventoryService, Depends(_service)],
) -> WriteOffRead:
    wo = await service.write_off(
        batch_id=batch_id,
        qty=payload.qty,
        reason=payload.reason,
        comment=payload.comment,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("batches.write_off"),
    )
    return WriteOffRead.model_validate(wo)
