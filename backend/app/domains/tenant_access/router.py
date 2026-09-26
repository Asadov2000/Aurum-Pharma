"""Tenant read access and privileged, recently confirmed policy management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    current_user,
    get_db,
    require_platform_capability,
    require_recent_platform_capability,
)
from app.core.errors import PermissionDeniedError
from app.domains.tenant_access.repository import TenantAccessRepository
from app.domains.tenant_access.schemas import (
    AccessPolicyRead,
    AccessPolicyTenantRead,
    AccessPolicyUpdate,
)
from app.domains.tenant_access.service import TenantAccessService

router = APIRouter(prefix="/api/v1", tags=["tenant-access"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> TenantAccessService:
    return TenantAccessService(TenantAccessRepository(db))


@router.get("/admin/tenants/{tenant_id}/access-policy", response_model=AccessPolicyRead)
async def read_admin_policy(
    tenant_id: UUID,
    response: Response,
    user: Annotated[CurrentUser, Depends(require_platform_capability("platform.tenants.view"))],
    service: Annotated[TenantAccessService, Depends(_service)],
) -> AccessPolicyRead:
    response.headers["Cache-Control"] = "private, no-store"
    return AccessPolicyRead.model_validate(await service.get(tenant_id))


@router.patch("/admin/tenants/{tenant_id}/access-policy", response_model=AccessPolicyRead)
async def update_policy(
    tenant_id: UUID,
    payload: AccessPolicyUpdate,
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_recent_platform_capability("platform.tenants.manage"))
    ],
    service: Annotated[TenantAccessService, Depends(_service)],
) -> AccessPolicyRead:
    response.headers["Cache-Control"] = "private, no-store"
    return AccessPolicyRead.model_validate(await service.update(tenant_id, payload))


@router.get("/tenant/access-policy", response_model=AccessPolicyTenantRead)
async def read_tenant_policy(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[TenantAccessService, Depends(_service)],
) -> AccessPolicyTenantRead:
    response.headers["Cache-Control"] = "private, no-store"
    if user.tenant_id is None:
        raise PermissionDeniedError("Требуется контекст аптеки")
    return AccessPolicyTenantRead.model_validate(await service.get(user.tenant_id))
