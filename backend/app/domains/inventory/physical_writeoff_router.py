"""Write off physical stock and recover a committed result without resubmission."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import PermissionDeniedError
from app.domains.inventory.physical_writeoff_repository import PhysicalWriteOffRepository
from app.domains.inventory.physical_writeoff_schemas import (
    PhysicalWriteOffRead,
    PhysicalWriteOffRequest,
)
from app.domains.inventory.physical_writeoff_service import PhysicalWriteOffService

router = APIRouter(prefix="/physical/write-offs")


def _tenant(user: CurrentUser, branch_id: UUID) -> UUID:
    if user.tenant_id is None or not all(
        user.can_access_branch(permission, branch_id)
        for permission in ("batches.view", "batches.write_off")
    ):
        raise PermissionDeniedError("Physical write-off permission denied")
    return user.tenant_id


@router.post(
    "", response_model=PhysicalWriteOffRead, dependencies=[Depends(require_writable_tenant)]
)
async def post_physical_writeoff(
    payload: PhysicalWriteOffRequest,
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.write_off", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PhysicalWriteOffRead:
    tenant_id = _tenant(user, payload.branch_id)
    response.headers["Cache-Control"] = "private, no-store"
    return await PhysicalWriteOffService(PhysicalWriteOffRepository(db)).post(
        tenant_id=tenant_id, actor_id=user.user_id, request=payload
    )


@router.get("/operations/{operation_id}", response_model=PhysicalWriteOffRead)
async def read_physical_writeoff(
    operation_id: UUID,
    branch_id: Annotated[UUID, Query()],
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.write_off", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PhysicalWriteOffRead:
    tenant_id = _tenant(user, branch_id)
    response.headers["Cache-Control"] = "private, no-store"
    return await PhysicalWriteOffService(PhysicalWriteOffRepository(db)).read(
        tenant_id=tenant_id, branch_id=branch_id, operation_id=operation_id
    )
