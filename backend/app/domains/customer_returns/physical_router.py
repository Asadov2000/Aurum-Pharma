"""Read-only physical return queue, separate from the legacy disposition API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission
from app.core.errors import PermissionDeniedError
from app.domains.customer_returns.physical_repository import PhysicalCustomerReturnsRepository
from app.domains.customer_returns.physical_schemas import PhysicalCustomerReturnList
from app.domains.customer_returns.physical_service import PhysicalCustomerReturnsService

router = APIRouter()


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PhysicalCustomerReturnsService:
    return PhysicalCustomerReturnsService(PhysicalCustomerReturnsRepository(db))


@router.get("/physical", response_model=PhysicalCustomerReturnList)
async def list_physical_customer_returns(
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("customer_returns.view", policy="filter")),
    ],
    service: Annotated[PhysicalCustomerReturnsService, Depends(_service)],
    branch_id: Annotated[UUID | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    page: Annotated[int, Query(ge=1, le=2147483647)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PhysicalCustomerReturnList:
    if user.tenant_id is None:
        raise PermissionDeniedError("Tenant context is required")
    response.headers["Cache-Control"] = "private, no-store"
    return await service.list_returns(
        tenant_id=user.tenant_id,
        branch_id=branch_id,
        search=search,
        allowed_branch_ids=user.branch_scope_for("customer_returns.view"),
        page=page,
        page_size=page_size,
    )
