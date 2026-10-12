"""Read-only mixed return queue, separate from disposition and payout APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission
from app.core.errors import PermissionDeniedError
from app.domains.customer_returns.mixed_repository import MixedCustomerReturnsRepository
from app.domains.customer_returns.mixed_schemas import MixedCustomerReturnList
from app.domains.customer_returns.mixed_service import MixedCustomerReturnsService

router = APIRouter()


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> MixedCustomerReturnsService:
    return MixedCustomerReturnsService(MixedCustomerReturnsRepository(db))


@router.get("/mixed", response_model=MixedCustomerReturnList)
async def list_mixed_customer_returns(
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("customer_returns.view", policy="filter")),
    ],
    service: Annotated[MixedCustomerReturnsService, Depends(_service)],
    branch_id: Annotated[UUID | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    page: Annotated[int, Query(ge=1, le=2147483647)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MixedCustomerReturnList:
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
