"""FastAPI endpoints for the inventory domain."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission
from app.domains.inventory.expiry import ExpiryStatus
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.schemas import (
    BatchDetails,
    BatchList,
    BatchRead,
    BatchSummary,
    BatchWithExpiry,
    MovementRead,
    WriteOffCreate,
    WriteOffRead,
)
from app.domains.inventory.service import InventoryService

router = APIRouter(prefix="/api/v1/batches", tags=["inventory"])


def _can_view_batch_cost(user: CurrentUser, branch_id: UUID) -> bool:
    scope = user.branch_scope_for("batches.view_costs")
    return scope is None or branch_id in scope


def _can_view_summary_cost(
    user: CurrentUser,
    *,
    branch_id: UUID | None,
    visible_branch_scope: set[UUID] | None,
) -> bool:
    cost_scope = user.branch_scope_for("batches.view_costs")
    if cost_scope is None:
        return True
    if branch_id is not None:
        return branch_id in cost_scope
    return visible_branch_scope is not None and visible_branch_scope.issubset(cost_scope)


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> InventoryService:
    return InventoryService(InventoryRepository(db))


@router.get("", response_model=BatchList)
async def list_batches(
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("batches.view", policy="filter")),
    ],
    service: Annotated[InventoryService, Depends(_service)],
    catalog_id: Annotated[UUID | None, Query()] = None,
    branch_id: Annotated[UUID | None, Query()] = None,
    expiry_status: Annotated[ExpiryStatus | None, Query()] = None,
    batch_number: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    is_blocked: Annotated[bool | None, Query()] = None,
    show_empty: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> BatchList:
    branch_scope = user.branch_scope_for("batches.view")
    if branch_scope is not None and branch_id is not None and branch_id not in branch_scope:
        return BatchList(
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            summary=BatchSummary(
                total_qty=0,
                purchase_value=None,
                sale_value=0,
                attention_count=0,
                expired_count=0,
                blocked_count=0,
            ),
        )
    rows, summary = await service.list_batches(
        catalog_id=catalog_id,
        branch_id=branch_id,
        expiry_status=expiry_status,
        batch_number=batch_number,
        is_blocked=is_blocked,
        show_empty=show_empty,
        page=page,
        page_size=page_size,
        tenant_id=user.tenant_id,
        branch_ids=branch_scope,
    )
    return BatchList(
        items=[
            BatchWithExpiry(
                **BatchRead.model_validate(row.batch)
                .model_copy(
                    update={
                        "purchase_price": (
                            row.batch.purchase_price
                            if _can_view_batch_cost(user, row.batch.branch_id)
                            else None
                        )
                    }
                )
                .model_dump(),
                branch_name=row.branch_name,
                catalog_name=row.catalog_name,
                catalog_form=row.catalog_form,
                catalog_dosage=row.catalog_dosage,
                catalog_pack_size=row.catalog_pack_size,
                expiry_status=row.expiry_status,
                days_to_expiry=row.days_to_expiry,
            )
            for row in rows
        ],
        total=summary.total,
        page=page,
        page_size=page_size,
        summary=BatchSummary(
            total_qty=summary.total_qty,
            purchase_value=(
                summary.purchase_value
                if _can_view_summary_cost(
                    user,
                    branch_id=branch_id,
                    visible_branch_scope=branch_scope,
                )
                else None
            ),
            sale_value=summary.sale_value,
            attention_count=summary.attention_count,
            expired_count=summary.expired_count,
            blocked_count=summary.blocked_count,
        ),
    )


@router.get("/{batch_id}", response_model=BatchDetails)
async def get_batch(
    batch_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("batches.view", policy="resource")),
    ],
    service: Annotated[InventoryService, Depends(_service)],
) -> BatchDetails:
    branch_scope = user.branch_scope_for("batches.view")
    details, report_timezone, recent = await service.get_batch_details(
        batch_id,
        tenant_id=user.tenant_id,
        allowed_branch_ids=branch_scope,
    )
    return BatchDetails(
        **BatchRead.model_validate(details.batch)
        .model_copy(
            update={
                "purchase_price": (
                    details.batch.purchase_price
                    if _can_view_batch_cost(user, details.batch.branch_id)
                    else None
                )
            }
        )
        .model_dump(),
        branch_name=details.branch_name,
        catalog_name=details.catalog_name,
        catalog_form=details.catalog_form,
        catalog_dosage=details.catalog_dosage,
        catalog_pack_size=details.catalog_pack_size,
        expiry_status=details.expiry_status,
        days_to_expiry=details.days_to_expiry,
        report_timezone=report_timezone,
        recent_movements=[MovementRead.model_validate(m) for m in recent],
    )


@router.get("/{batch_id}/movements", response_model=list[MovementRead])
async def list_movements(
    batch_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("batches.view", policy="resource")),
    ],
    service: Annotated[InventoryService, Depends(_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[MovementRead]:
    movements = await service.list_movements(
        batch_id,
        tenant_id=user.tenant_id,
        limit=limit,
        allowed_branch_ids=user.branch_scope_for("batches.view"),
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
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("batches.write_off", policy="resource")),
    ],
    service: Annotated[InventoryService, Depends(_service)],
) -> WriteOffRead:
    wo = await service.write_off(
        batch_id=batch_id,
        operation_id=payload.operation_id,
        qty=payload.qty,
        reason=payload.reason,
        comment=payload.comment,
        actor_id=user.user_id,
        tenant_id=user.tenant_id,
        allowed_branch_ids=user.branch_scope_for("batches.write_off"),
    )
    result = WriteOffRead.model_validate(wo)
    if not _can_view_batch_cost(user, wo.branch_id):
        result = result.model_copy(update={"amount": None})
    return result
