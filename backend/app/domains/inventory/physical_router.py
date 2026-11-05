"""Branch-scoped physical inventory reads and explicit restriction changes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import PermissionDeniedError
from app.domains.inventory.physical_repository import PhysicalInventoryRepository
from app.domains.inventory.physical_schemas import (
    PhysicalLotBlockRead,
    PhysicalLotBlockRequest,
    PhysicalLotList,
)
from app.domains.inventory.physical_writeoff_stock_repository import PhysicalWriteOffStockRepository
from app.domains.inventory.physical_writeoff_stock_schemas import PhysicalWriteOffStockRead
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_physical_blocking import PhysicalLotBlockingService

router = APIRouter(prefix="/physical")


def _tenant(user: CurrentUser, permission: str, branch_id: UUID) -> UUID:
    if user.tenant_id is None or not user.can_access_branch(permission, branch_id):
        raise PermissionDeniedError("Branch access denied")
    return user.tenant_id


@router.get("", response_model=PhysicalLotList)
async def list_physical_lots(
    branch_id: Annotated[UUID, Query()],
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.view", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    catalog_id: Annotated[UUID | None, Query()] = None,
    is_blocked: Annotated[bool | None, Query()] = None,
    show_empty: Annotated[bool, Query()] = False,
    q: Annotated[str, Query(max_length=200)] = "",
    page: Annotated[int, Query(ge=1, le=2147483647)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PhysicalLotList:
    response.headers["Cache-Control"] = "private, no-store"
    return await PhysicalInventoryRepository(db).list_lots(
        tenant_id=_tenant(user, "batches.view", branch_id),
        actor_id=user.user_id,
        branch_id=branch_id,
        catalog_id=catalog_id,
        is_blocked=is_blocked,
        show_empty=show_empty,
        q=q.strip(),
        page=page,
        page_size=page_size,
    )


@router.get("/{lot_id}/write-off-stock", response_model=PhysicalWriteOffStockRead)
async def read_physical_writeoff_stock(
    lot_id: UUID,
    branch_id: Annotated[UUID, Query()],
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.write_off", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    page: Annotated[int, Query(ge=1, le=2147483647)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PhysicalWriteOffStockRead:
    tenant_id = _tenant(user, "batches.view", branch_id)
    _tenant(user, "batches.write_off", branch_id)
    response.headers["Cache-Control"] = "private, no-store"
    return await PhysicalWriteOffStockRepository(db).read(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        branch_id=branch_id,
        lot_id=lot_id,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/{lot_id}/blocking",
    response_model=PhysicalLotBlockRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def block_physical_lot(
    lot_id: UUID,
    payload: PhysicalLotBlockRequest,
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.update", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PhysicalLotBlockRead:
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id = _tenant(user, "batches.update", payload.branch_id)
    _tenant(user, "batches.view", payload.branch_id)
    saved = await PhysicalLotBlockingService(UnitDispensingV2Repository(db)).set_blocked(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        branch_id=payload.branch_id,
        lot_id=lot_id,
        allowed_branch_ids={payload.branch_id},
        is_blocked=payload.is_blocked,
        expected_blocking_revision=int(payload.expected_blocking_revision),
    )
    return PhysicalLotBlockRead(
        lot_id=saved.lot_id,
        branch_id=saved.branch_id,
        is_blocked=saved.is_blocked,
        blocking_revision=str(saved.blocking_revision),
    )
