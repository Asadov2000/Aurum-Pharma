"""HTTP API for quarantined customer returns."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    get_db,
    require_branch_permission,
    require_writable_tenant,
)
from app.core.errors import PermissionDeniedError
from app.domains.customer_returns.repository import CustomerReturnsRepository
from app.domains.customer_returns.schemas import (
    CustomerReturnList,
    CustomerReturnRead,
    CustomerReturnResolve,
    CustomerReturnStatus,
)
from app.domains.customer_returns.service import CustomerReturnsService

router = APIRouter(prefix="/api/v1/customer-returns", tags=["customer-returns"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> CustomerReturnsService:
    return CustomerReturnsService(CustomerReturnsRepository(db))


def _tenant_id(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise PermissionDeniedError("Tenant context is required")
    return user.tenant_id


@router.get("", response_model=CustomerReturnList)
async def list_customer_returns(
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("customer_returns.view", policy="filter")),
    ],
    service: Annotated[CustomerReturnsService, Depends(_service)],
    status: Annotated[CustomerReturnStatus | None, Query()] = None,
    branch_id: Annotated[UUID | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CustomerReturnList:
    branch_scope = user.branch_scope_for("customer_returns.view")
    if branch_scope is not None and branch_id is not None and branch_id not in branch_scope:
        return CustomerReturnList(
            items=[], total=0, pending=0, resolved=0, page=page, page_size=page_size
        )
    items, total, pending, resolved = await service.list_returns(
        tenant_id=_tenant_id(user),
        status=status,
        branch_id=branch_id,
        search=search,
        allowed_branch_ids=branch_scope,
        page=page,
        page_size=page_size,
    )
    return CustomerReturnList(
        items=items,
        total=total,
        pending=pending,
        resolved=resolved,
        page=page,
        page_size=page_size,
    )


@router.post("/{item_id}/resolve", response_model=CustomerReturnRead)
async def resolve_customer_return(
    item_id: UUID,
    payload: CustomerReturnResolve,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("customer_returns.resolve", policy="resource")),
    ],
    _writable: Annotated[CurrentUser, Depends(require_writable_tenant)],
    service: Annotated[CustomerReturnsService, Depends(_service)],
) -> CustomerReturnRead:
    return await service.resolve(
        tenant_id=_tenant_id(user),
        item_id=item_id,
        operation_id=payload.operation_id,
        disposition_type=payload.disposition_type,
        reason_code=payload.reason_code,
        comment=payload.comment,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("customer_returns.resolve"),
    )
