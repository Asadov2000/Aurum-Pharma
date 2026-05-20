"""FastAPI endpoints for suppliers + supplier_returns."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_permission
from app.core.errors import BusinessRuleError
from app.domains.suppliers.repository import SuppliersRepository
from app.domains.suppliers.schemas import (
    SupplierCreate,
    SupplierRead,
    SupplierReturnCreate,
    SupplierReturnCreated,
    SupplierReturnRead,
    SupplierUpdate,
)
from app.domains.suppliers.service import SuppliersService

router = APIRouter(prefix="/api/v1/suppliers", tags=["suppliers"])


async def _service(db: Annotated[AsyncSession, Depends(get_db)]) -> SuppliersService:
    return SuppliersService(SuppliersRepository(db))


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


# ---- supplier returns (declared first so the router resolves /returns
#      before the generic /{supplier_id} routes) ----


@router.post(
    "/returns",
    response_model=SupplierReturnCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier_return(
    payload: SupplierReturnCreate,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.return"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierReturnCreated:
    sr, warning = await service.create_return(
        tenant_id=_current_tenant_or_400(user),
        supplier_id=payload.supplier_id,
        batch_id=payload.batch_id,
        qty=payload.qty,
        reason=payload.reason,
        comment=payload.comment,
        source_document_id=payload.source_document_id,
        actor_id=user.user_id,
    )
    return SupplierReturnCreated(
        **SupplierReturnRead.model_validate(sr).model_dump(),
        warning=warning,
    )


@router.get("/returns", response_model=list[SupplierReturnRead])
async def list_supplier_returns(
    _user: Annotated[CurrentUser, Depends(require_permission("suppliers.view"))],
    service: Annotated[SuppliersService, Depends(_service)],
    supplier_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
) -> list[SupplierReturnRead]:
    items = await service.list_returns(
        supplier_id=supplier_id, date_from=date_from, date_to=date_to
    )
    return [SupplierReturnRead.model_validate(i) for i in items]


# ---- supplier CRUD ----


@router.get("", response_model=list[SupplierRead])
async def list_suppliers(
    _user: Annotated[CurrentUser, Depends(require_permission("suppliers.view"))],
    service: Annotated[SuppliersService, Depends(_service)],
    include_inactive: Annotated[bool, Query()] = False,
) -> list[SupplierRead]:
    items = await service.list_suppliers(include_inactive=include_inactive)
    return [SupplierRead.model_validate(i) for i in items]


@router.post("", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    payload: SupplierCreate,
    user: Annotated[CurrentUser, Depends(require_permission("suppliers.create"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierRead:
    supplier = await service.create_supplier(
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(exclude_none=True),
        created_by=user.user_id,
    )
    return SupplierRead.model_validate(supplier)


@router.patch("/{supplier_id}", response_model=SupplierRead)
async def update_supplier(
    supplier_id: UUID,
    payload: SupplierUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("suppliers.update"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierRead:
    supplier = await service.update_supplier(
        supplier_id,
        fields=payload.model_dump(exclude_none=True),
        updated_by=user.user_id,
    )
    return SupplierRead.model_validate(supplier)
