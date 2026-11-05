"""Scoped physical quarantine decisions and immutable, cost-free history."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import PermissionDeniedError
from app.domains.customer_returns.physical_disposition import PhysicalDispositionRequest
from app.domains.customer_returns.physical_disposition_repository import (
    PhysicalDispositionRepository,
)
from app.domains.customer_returns.physical_disposition_schemas import (
    PhysicalDispositionHistory,
    PhysicalDispositionStatus,
    ResolvedPhysicalDisposition,
)
from app.domains.customer_returns.physical_disposition_service import PhysicalDispositionService

router = APIRouter(prefix="/physical/dispositions")


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PhysicalDispositionService:
    return PhysicalDispositionService(PhysicalDispositionRepository(db))


def _tenant_id(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise PermissionDeniedError("Tenant context is required")
    return user.tenant_id


@router.get("/operations/{decision_operation_id}", response_model=PhysicalDispositionStatus)
async def read_physical_disposition_status(
    decision_operation_id: UUID,
    branch_id: Annotated[UUID, Query()],
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("customer_returns.resolve", policy="resource")),
    ],
    service: Annotated[PhysicalDispositionService, Depends(_service)],
) -> PhysicalDispositionStatus:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.read_status(
        tenant_id=_tenant_id(user),
        branch_id=branch_id,
        decision_operation_id=decision_operation_id,
        allowed_branch_ids=user.branch_scope_for("customer_returns.resolve"),
    )


@router.post(
    "",
    response_model=ResolvedPhysicalDisposition,
    dependencies=[Depends(require_writable_tenant)],
)
async def resolve_physical_customer_return(
    payload: PhysicalDispositionRequest,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("customer_returns.resolve", policy="resource")),
    ],
    service: Annotated[PhysicalDispositionService, Depends(_service)],
) -> ResolvedPhysicalDisposition:
    response.headers["Cache-Control"] = "private, no-store"
    # The DB writer independently checks the current actor and the source branch,
    # including every replay; queue/view permission is deliberately not required.
    return await service.resolve(tenant_id=_tenant_id(user), request=payload)


@router.get("/history", response_model=PhysicalDispositionHistory)
async def list_physical_dispositions(
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("customer_returns.view", policy="filter")),
    ],
    service: Annotated[PhysicalDispositionService, Depends(_service)],
    branch_id: Annotated[UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=2147483647)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PhysicalDispositionHistory:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.list_history(
        tenant_id=_tenant_id(user),
        branch_id=branch_id,
        allowed_branch_ids=user.branch_scope_for("customer_returns.view"),
        page=page,
        page_size=page_size,
    )
