"""Authenticated local Edge read-model endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import (
    CurrentUser,
    get_db,
    require_any_branch_permission,
    require_branch_permission,
    require_permission,
)
from app.core.errors import PermissionDeniedError, ServiceUnavailableError
from app.domains.sync.read_model import (
    CatalogDetailsReadModel,
    CatalogReadModel,
    FoundationReadModel,
    InventoryReadModel,
    LocalReadModel,
    SupplierReadModel,
)
from app.domains.sync.readiness import edge_snapshot_readiness
from app.domains.sync.repository import SyncEdgeRepository

router = APIRouter(prefix="/api/v1/edge/read-model", tags=["edge-read-model"])


def _ensure_edge_profile() -> None:
    if get_settings().DEPLOYMENT_PROFILE != "edge_shadow":
        raise ServiceUnavailableError("Local Edge read model is unavailable in Cloud profile")


def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> LocalReadModel:
    _ensure_edge_profile()
    return LocalReadModel(
        SyncEdgeRepository(db), readiness_guard=edge_snapshot_readiness.require_verified
    )


def _tenant_id(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise PermissionDeniedError("Tenant context is required")
    return user.tenant_id


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/foundation", response_model=FoundationReadModel)
async def foundation(
    branch_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("branches.view", policy="resource")),
    ],
    service: Annotated[LocalReadModel, Depends(_service)],
) -> FoundationReadModel:
    if not user.can_access_branch("branches.view", branch_id):
        raise PermissionDeniedError("Branch is outside the assigned scope")
    _no_store(response)
    return await service.foundation(tenant_id=_tenant_id(user), branch_id=branch_id)


@router.get("/catalog", response_model=CatalogReadModel)
async def catalog(
    branch_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_any_branch_permission("catalog.view", "pos.sell", policy="filter")),
    ],
    service: Annotated[LocalReadModel, Depends(_service)],
    q: Annotated[str | None, Query(max_length=500)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CatalogReadModel:
    if not user.can_access_branch_for_any(branch_id, "catalog.view", "pos.sell"):
        raise PermissionDeniedError("Branch is outside the assigned scope")
    _no_store(response)
    return await service.catalog(
        tenant_id=_tenant_id(user),
        branch_id=branch_id,
        query=q,
        page=page,
        page_size=page_size,
    )


@router.get("/catalog/by-barcode/{barcode}", response_model=CatalogDetailsReadModel)
async def catalog_by_barcode(
    barcode: str,
    branch_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_any_branch_permission("catalog.view", "pos.sell", policy="filter")),
    ],
    service: Annotated[LocalReadModel, Depends(_service)],
) -> CatalogDetailsReadModel:
    if not user.can_access_branch_for_any(branch_id, "catalog.view", "pos.sell"):
        raise PermissionDeniedError("Branch is outside the assigned scope")
    _no_store(response)
    return await service.catalog_details(
        tenant_id=_tenant_id(user), branch_id=branch_id, barcode=barcode
    )


@router.get("/catalog/{item_id}", response_model=CatalogDetailsReadModel)
async def catalog_details(
    item_id: UUID,
    branch_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_any_branch_permission("catalog.view", "pos.sell", policy="filter")),
    ],
    service: Annotated[LocalReadModel, Depends(_service)],
) -> CatalogDetailsReadModel:
    if not user.can_access_branch_for_any(branch_id, "catalog.view", "pos.sell"):
        raise PermissionDeniedError("Branch is outside the assigned scope")
    _no_store(response)
    return await service.catalog_details(
        tenant_id=_tenant_id(user), branch_id=branch_id, item_id=item_id
    )


@router.get("/suppliers", response_model=SupplierReadModel)
async def suppliers(
    branch_id: UUID,
    response: Response,
    user: Annotated[CurrentUser, Depends(require_permission("suppliers.view"))],
    service: Annotated[LocalReadModel, Depends(_service)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SupplierReadModel:
    if not user.can_access_branch("suppliers.view", branch_id):
        raise PermissionDeniedError("Branch is outside the assigned scope")
    _no_store(response)
    return await service.suppliers(
        tenant_id=_tenant_id(user),
        branch_id=branch_id,
        query=q,
        page=page,
        page_size=page_size,
    )


@router.get("/inventory", response_model=InventoryReadModel)
async def inventory(
    branch_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("batches.view", policy="filter")),
    ],
    service: Annotated[LocalReadModel, Depends(_service)],
    show_empty: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> InventoryReadModel:
    if not user.can_access_branch("batches.view", branch_id):
        raise PermissionDeniedError("Branch is outside the assigned scope")
    _no_store(response)
    return await service.inventory(
        tenant_id=_tenant_id(user),
        branch_id=branch_id,
        can_view_costs=user.can_access_branch("batches.view_costs", branch_id),
        show_empty=show_empty,
        page=page,
        page_size=page_size,
    )
